# -*- coding: utf-8 -*-
# conversation_embeddings（条件付きFK） + MV(congestion_by_date_spot は親揃い時のみ作成)
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_embeddings_mv"
down_revision = "0002_pre_guides"
branch_labels = None
depends_on = None

EMB_TABLE = "conversation_embeddings"
EMB_IDX = "ix_convemb_session_ts"
FK_SESS = "fk_convemb_session_id"

MV_NAME = "congestion_by_date_spot"
MV_UNIQUE_INDEX = "ux_congestion_by_date_spot"

def _has_table(inspector, name: str) -> bool:
    return name in inspector.get_table_names()

def _has_fk(conn, table: str, fk_name: str) -> bool:
    sql = sa.text("""
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        WHERE t.relname = :table AND c.conname = :fk AND c.contype = 'f'
        LIMIT 1
    """)
    return bool(conn.execute(sql, {"table": table, "fk": fk_name}).scalar())

def _mv_exists(conn, name: str) -> bool:
    return bool(conn.execute(sa.text(
        "SELECT 1 FROM pg_matviews WHERE matviewname = :n LIMIT 1"
    ), {"n": name}).scalar())

def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    has_sessions = _has_table(inspector, "sessions")
    has_plans    = _has_table(inspector, "plans")
    has_stops    = _has_table(inspector, "stops")

    # 0) ENUM 'speaker' を「無ければ作成」：IF NOT EXISTS 相当
    op.execute(sa.text("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'speaker') THEN
            CREATE TYPE speaker AS ENUM ('user', 'assistant', 'system');
        END IF;
    END$$;
    """))

    # 1) conversation_embeddings
    #    ここでは ENUM を直接使わず、一旦 VARCHAR で作成 → 直後に ALTER で ENUM へ変換
    if EMB_TABLE not in inspector.get_table_names():
        op.create_table(
            EMB_TABLE,
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True, nullable=False),
            sa.Column("session_id", sa.String(length=64), nullable=False, index=True),
            sa.Column("speaker", sa.String(length=16), nullable=False),  # ← まずは文字列で作る
            sa.Column("lang", sa.String(length=8), nullable=True),
            sa.Column("text", sa.Text, nullable=False),
            sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("embedding_version", sa.String(length=64), nullable=False, server_default=sa.text("'mxbai-embed-large'")),
            sa.Column("embedding", sa.JSON, nullable=False),
        )
        # 作成直後に ENUM へ型変換（テーブルはまだ空なので安全）
        op.execute(sa.text(
            f"ALTER TABLE {EMB_TABLE} "
            f"ALTER COLUMN speaker TYPE speaker USING speaker::speaker"
        ))

    # インデックス
    if EMB_IDX not in [ix["name"] for ix in inspector.get_indexes(EMB_TABLE)]:
        op.create_index(EMB_IDX, EMB_TABLE, ["session_id", "ts"], unique=False)

    # FK（sessions がある場合のみ）
    if has_sessions and not _has_fk(bind, EMB_TABLE, FK_SESS):
        op.create_foreign_key(FK_SESS, EMB_TABLE, "sessions", ["session_id"], ["id"], ondelete="CASCADE")

    # 2) 混雑マテビューは plans & stops が揃っているときだけ作成
    if has_plans and has_stops and not _mv_exists(bind, MV_NAME):
        op.execute(sa.text(f"""
            CREATE MATERIALIZED VIEW {MV_NAME} AS
            SELECT
                p.start_date::date AS visit_date,
                s.spot_id::int     AS spot_id,
                COUNT(*)::int      AS plan_count
            FROM plans p
            JOIN stops s ON s.plan_id = p.id
            WHERE p.start_date IS NOT NULL
            GROUP BY p.start_date, s.spot_id
        """))
        op.execute(sa.text(f"CREATE UNIQUE INDEX {MV_UNIQUE_INDEX} ON {MV_NAME} (visit_date, spot_id)"))

def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # MV
    try:
        op.execute(sa.text(f"DROP INDEX IF EXISTS {MV_UNIQUE_INDEX}"))
        op.execute(sa.text(f"DROP MATERIALIZED VIEW IF EXISTS {MV_NAME}"))
    except Exception:
        pass

    # conversation_embeddings
    try:
        if _has_fk(bind, EMB_TABLE, FK_SESS):
            op.drop_constraint(FK_SESS, EMB_TABLE, type_="foreignkey")
    except Exception:
        pass

    try:
        op.drop_index(EMB_IDX, table_name=EMB_TABLE)
    except Exception:
        pass

    if EMB_TABLE in inspector.get_table_names():
        op.drop_table(EMB_TABLE)

    # ENUM 'speaker' は、他で使われていなければ落とす
    op.execute(sa.text("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_attribute a
            JOIN pg_type t ON a.atttypid = t.oid
            WHERE t.typname = 'speaker'
        ) THEN
            DROP TYPE IF EXISTS speaker;
        END IF;
    END$$;
    """))
