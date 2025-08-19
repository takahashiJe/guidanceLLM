# -*- coding: utf-8 -*-
"""Align models.py with DB: add sessions.app_status/active_plan_id, relax plans.start_date,
rename stops.position->order_index, add missing indexes.

Revision ID: 0011_align_models
Revises: 0010_finalize_fks_and_mv
Create Date: 2025-08-21 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0011_align_models"
down_revision = "0010_finalize_fks_and_mv"
branch_labels = None
depends_on = None


def _has_table(inspector, table: str) -> bool:
    return table in inspector.get_table_names()

def _has_column(inspector, table: str, column: str) -> bool:
    try:
        return any(c["name"] == column for c in inspector.get_columns(table))
    except Exception:
        return False

def _has_index(inspector, table: str, index: str) -> bool:
    try:
        return any(ix.get("name") == index for ix in inspector.get_indexes(table))
    except Exception:
        return False

def _has_uc(inspector, table: str, name: str) -> bool:
    try:
        return any(uc.get("name") == name for uc in inspector.get_unique_constraints(table))
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 1) sessions: add app_status, active_plan_id (nullable) and backfill FK to plans/users if possible
    if _has_table(inspector, "sessions"):
        if not _has_column(inspector, "sessions", "app_status"):
            op.add_column("sessions", sa.Column("app_status", sa.String(length=64), nullable=True))
        if not _has_column(inspector, "sessions", "active_plan_id"):
            op.add_column("sessions", sa.Column("active_plan_id", sa.Integer(), nullable=True))
        # FK -> plans
        if _has_table(inspector, "plans") and _has_column(inspector, "sessions", "active_plan_id"):
            fks = inspector.get_foreign_keys("sessions")
            if not any(fk.get("constrained_columns") == ["active_plan_id"] for fk in fks):
                op.create_foreign_key(
                    "fk_sessions_active_plan_id_plans",
                    source_table="sessions",
                    referent_table="plans",
                    local_cols=["active_plan_id"],
                    remote_cols=["id"],
                    ondelete=None,
                )
        # FK -> users（型が合わない場合はスキップ）
        if _has_table(inspector, "users") and _has_column(inspector, "sessions", "user_id"):
            fks = inspector.get_foreign_keys("sessions")
            if not any(fk.get("constrained_columns") == ["user_id"] for fk in fks):
                try:
                    op.create_foreign_key(
                        "fk_sessions_user_id_users",
                        source_table="sessions",
                        referent_table="users",
                        local_cols=["user_id"],
                        remote_cols=["id"],
                        ondelete="SET NULL",
                    )
                except Exception:
                    pass

    # 2) plans.start_date -> nullable True + index
    if _has_table(inspector, "plans") and _has_column(inspector, "plans", "start_date"):
        op.alter_column("plans", "start_date", existing_type=sa.Date(), nullable=True)
        if not _has_index(inspector, "plans", "ix_plans_user_date"):
            op.create_index("ix_plans_user_date", "plans", ["user_id", "start_date"], unique=False)

    # 3) stops.position -> order_index, add UC + index
    if _has_table(inspector, "stops"):
        cols = [c["name"] for c in inspector.get_columns("stops")]
        if "position" in cols and "order_index" not in cols:
            op.alter_column("stops", "position", new_column_name="order_index")
        if not _has_uc(inspector, "stops", "uq_stops_plan_order"):
            op.create_unique_constraint("uq_stops_plan_order", "stops", ["plan_id", "order_index"])
        if not _has_index(inspector, "stops", "ix_stops_plan_order"):
            op.create_index("ix_stops_plan_order", "stops", ["plan_id", "order_index"], unique=False)

    # 4) spots: composite index (spot_type, official_name)
    if _has_table(inspector, "spots") and not _has_index(inspector, "spots", "ix_spots_type_name"):
        op.create_index("ix_spots_type_name", "spots", ["spot_type", "official_name"], unique=False)

    # 5) OPTIONAL: sessions.user_id を Integer に寄せる（失敗時は黙ってスキップ）
    try:
        if _has_table(inspector, "sessions") and _has_column(inspector, "sessions", "user_id"):
            cols = inspector.get_columns("sessions")
            col = next((c for c in cols if c["name"] == "user_id"), None)
            if col and isinstance(col.get("type"), sa.BigInteger):
                op.execute("ALTER TABLE sessions ALTER COLUMN user_id TYPE INTEGER USING user_id::integer")
    except Exception:
        pass


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, "spots") and _has_index(inspector, "spots", "ix_spots_type_name"):
        op.drop_index("ix_spots_type_name", table_name="spots")

    if _has_table(inspector, "stops") and _has_index(inspector, "stops", "ix_stops_plan_order"):
        op.drop_index("ix_stops_plan_order", table_name="stops")
    if _has_table(inspector, "stops") and _has_uc(inspector, "stops", "uq_stops_plan_order"):
        op.drop_constraint("uq_stops_plan_order", "stops", type_="unique")

    if _has_table(inspector, "plans") and _has_index(inspector, "plans", "ix_plans_user_date"):
        op.drop_index("ix_plans_user_date", table_name="plans")
    # start_date の NOT NULL 差し戻しは安全性から行いません

    if _has_table(inspector, "sessions") and _has_column(inspector, "sessions", "active_plan_id"):
        try:
            fks = inspector.get_foreign_keys("sessions")
            for fk in fks:
                if fk.get("constrained_columns") == ["active_plan_id"] and fk.get("name"):
                    op.drop_constraint(fk.get("name"), "sessions", type_="foreignkey")
        except Exception:
            pass
        op.drop_column("sessions", "active_plan_id")
    if _has_table(inspector, "sessions") and _has_column(inspector, "sessions", "app_status"):
        op.drop_column("sessions", "app_status")
