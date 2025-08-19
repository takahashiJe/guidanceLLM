# -*- coding: utf-8 -*-
# 後付け FK & MV（親テーブルが揃った後に一度だけ実行される）
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_finalize_fks_mv"
down_revision = "0004_ap_table"
branch_labels = None
depends_on = None

PG_TABLE = "pre_generated_guides"
PG_FK_SESS = "fk_pre_guides_session_id"
PG_FK_SPOT = "fk_pre_guides_spot_id"

EMB_TABLE = "conversation_embeddings"
EMB_FK_SESS = "fk_convemb_session_id"

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
    has_spots    = _has_table(inspector, "spots")
    has_plans    = _has_table(inspector, "plans")
    has_stops    = _has_table(inspector, "stops")

    # pre_generated_guides の FK 追付け
    if _has_table(inspector, PG_TABLE):
        if has_sessions and not _has_fk(bind, PG_TABLE, PG_FK_SESS):
            op.create_foreign_key(PG_FK_SESS, PG_TABLE, "sessions",
                                  ["session_id"], ["id"], ondelete="CASCADE")
        if has_spots and not _has_fk(bind, PG_TABLE, PG_FK_SPOT):
            op.create_foreign_key(PG_FK_SPOT, PG_TABLE, "spots",
                                  ["spot_id"], ["id"], ondelete="CASCADE")

    # conversation_embeddings の FK 追付け
    if _has_table(inspector, EMB_TABLE):
        if has_sessions and not _has_fk(bind, EMB_TABLE, EMB_FK_SESS):
            op.create_foreign_key(EMB_FK_SESS, EMB_TABLE, "sessions",
                                  ["session_id"], ["id"], ondelete="CASCADE")

    # MV が未作成で、plans/stops が揃っていれば作成
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
    # MV
    try:
        op.execute(sa.text(f"DROP INDEX IF EXISTS {MV_UNIQUE_INDEX}"))
        op.execute(sa.text(f"DROP MATERIALIZED VIEW IF EXISTS {MV_NAME}"))
    except Exception:
        pass
    # FK
    for table, fk in [
        (PG_TABLE, PG_FK_SESS), (PG_TABLE, PG_FK_SPOT),
        (EMB_TABLE, EMB_FK_SESS),
    ]:
        try:
            op.drop_constraint(fk, table, type_="foreignkey")
        except Exception:
            pass
