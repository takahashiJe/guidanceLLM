# -*- coding: utf-8 -*-
# plans/stops が揃ったタイミングで MV(congestion_by_date_spot) を作成
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006_create_mv"
down_revision = "0005_finalize_fks_mv"
branch_labels = None
depends_on = None

MV_NAME = "congestion_by_date_spot"
MV_UNIQUE_INDEX = "ux_congestion_by_date_spot"

def _has_table(inspector, name: str) -> bool:
    return name in inspector.get_table_names()

def _mv_exists(conn, name: str) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM pg_matviews WHERE matviewname = :n LIMIT 1"),
            {"n": name},
        ).scalar()
    )

def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    has_plans = _has_table(inspector, "plans")
    has_stops = _has_table(inspector, "stops")

    # ← 修正: Python では 'and' を使う
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
    try:
        op.execute(sa.text(f"DROP INDEX IF EXISTS {MV_UNIQUE_INDEX}"))
        op.execute(sa.text(f"DROP MATERIALIZED VIEW IF EXISTS {MV_NAME}"))
    except Exception:
        pass
