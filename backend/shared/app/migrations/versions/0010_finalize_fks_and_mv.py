"""finalize FKs for guides/embeddings and (re)create congestion MV

Revision ID: 0010_finalize_fks_and_mv
Revises: 0009_sessions_plans_stops
Create Date: 2025-08-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

# revision identifiers, used by Alembic.
revision = "0010_finalize_fks_and_mv"
down_revision = "0009_sessions_plans_stops"
branch_labels = None
depends_on = None


def _fk_exists(conn, table: str, fk_name: str) -> bool:
    insp = Inspector.from_engine(conn)
    for fk in insp.get_foreign_keys(table):
        if fk.get("name") == fk_name:
            return True
    return False


def upgrade():
    conn = op.get_bind()
    insp = Inspector.from_engine(conn)
    tables = set(insp.get_table_names())

    # -----------------------
    # 後付けFK: pre_generated_guides.session_id → sessions.id
    # -----------------------
    if "pre_generated_guides" in tables and "sessions" in tables:
        fk_name = "fk_pre_guides_session_id_sessions"
        if not _fk_exists(conn, "pre_generated_guides", fk_name):
            op.create_foreign_key(
                fk_name,
                "pre_generated_guides",
                "sessions",
                ["session_id"],
                ["id"],
                ondelete="CASCADE",
            )

    # -----------------------
    # 後付けFK: conversation_embeddings.session_id → sessions.id
    # -----------------------
    if "conversation_embeddings" in tables and "sessions" in tables:
        fk_name = "fk_convemb_session_id_sessions"
        if not _fk_exists(conn, "conversation_embeddings", fk_name):
            op.create_foreign_key(
                fk_name,
                "conversation_embeddings",
                "sessions",
                ["session_id"],
                ["id"],
                ondelete="CASCADE",
            )

    # -----------------------
    # マテビュー: congestion_by_date_spot
    #   plans / stops が出揃ったので（未作成なら）作る
    # -----------------------
    if {"plans", "stops"} <= tables:
        # 既に存在する可能性があるので IF NOT EXISTS で安全に
        op.execute(
            """
            CREATE MATERIALIZED VIEW IF NOT EXISTS congestion_by_date_spot AS
            SELECT
                s.spot_id,
                p.start_date::date AS date,
                COUNT(*)::bigint AS plan_count
            FROM stops s
            JOIN plans p ON p.id = s.plan_id
            GROUP BY s.spot_id, p.start_date::date
            """
        )
        # 主キー相当のユニークインデックス
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_congestion_by_date_spot ON congestion_by_date_spot (spot_id, date)"
        )
        # 参照用の補助インデックス
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_congestion_date ON congestion_by_date_spot (date)"
        )


def downgrade():
    # MVとインデックスを撤去
    op.execute("DROP INDEX IF EXISTS ix_congestion_date")
    op.execute("DROP INDEX IF EXISTS uq_congestion_by_date_spot")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS congestion_by_date_spot")

    # 後付けFKを撤去
    for table, fk in [
        ("pre_generated_guides", "fk_pre_guides_session_id_sessions"),
        ("conversation_embeddings", "fk_convemb_session_id_sessions"),
    ]:
        try:
            op.drop_constraint(fk, table_name=table, type_="foreignkey")
        except Exception:
            pass
