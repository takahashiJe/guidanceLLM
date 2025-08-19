"""create sessions / plans / stops core tables

Revision ID: 0009_sessions_plans_stops
Revises: 0008_create_spots_table
Create Date: 2025-08-20

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

# revision identifiers, used by Alembic.
revision = "0009_sessions_plans_stops"
down_revision = "0008_create_spots_table"
branch_labels = None
depends_on = None


def _has_table(conn, name: str) -> bool:
    insp = Inspector.from_engine(conn)
    return name in insp.get_table_names()


def upgrade():
    conn = op.get_bind()

    # -----------------------
    # sessions
    # -----------------------
    if not _has_table(conn, "sessions"):
        op.create_table(
            "sessions",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.BigInteger(), nullable=True),  # ユーザー表が無くてもNULL可
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    # -----------------------
    # plans
    # -----------------------
    if not _has_table(conn, "plans"):
        op.create_table(
            "plans",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger(), nullable=True),
            sa.Column("session_id", sa.String(64), nullable=False),
            sa.Column("start_date", sa.Date(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        # sessions があればFKを後付け（Docker初回でも安全）
        insp = Inspector.from_engine(conn)
        if "sessions" in insp.get_table_names():
            op.create_foreign_key(
                "fk_plans_session_id_sessions",
                "plans",
                "sessions",
                ["session_id"],
                ["id"],
                ondelete="CASCADE",
            )
        # users テーブルは未定義のため、存在する場合のみFKを追加
        if "users" in insp.get_table_names():
            op.create_foreign_key(
                "fk_plans_user_id_users",
                "plans",
                "users",
                ["user_id"],
                ["id"],
                ondelete="SET NULL",
            )
        op.create_index("ix_plans_session_start", "plans", ["session_id", "start_date"])

    # -----------------------
    # stops
    # -----------------------
    if not _has_table(conn, "stops"):
        op.create_table(
            "stops",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column("plan_id", sa.BigInteger(), nullable=False),
            sa.Column("spot_id", sa.BigInteger(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.create_index("ix_stops_plan_pos", "stops", ["plan_id", "position"])
        op.create_unique_constraint("uq_stops_plan_position", "stops", ["plan_id", "position"])

        # 親があればFKを追加
        insp = Inspector.from_engine(conn)
        if "plans" in insp.get_table_names():
            op.create_foreign_key(
                "fk_stops_plan_id_plans",
                "stops",
                "plans",
                ["plan_id"],
                ["id"],
                ondelete="CASCADE",
            )
        if "spots" in insp.get_table_names():
            op.create_foreign_key(
                "fk_stops_spot_id_spots",
                "stops",
                "spots",
                ["spot_id"],
                ["id"],
                ondelete="CASCADE",
            )


def downgrade():
    # 依存順に削除
    with op.batch_alter_table("stops", schema=None) as batch_op:
        for fk in ("fk_stops_plan_id_plans", "fk_stops_spot_id_spots"):
            try:
                batch_op.drop_constraint(fk, type_="foreignkey")
            except Exception:
                pass
    op.drop_table("stops")

    with op.batch_alter_table("plans", schema=None) as batch_op:
        for fk in ("fk_plans_session_id_sessions", "fk_plans_user_id_users"):
            try:
                batch_op.drop_constraint(fk, type_="foreignkey")
            except Exception:
                pass
        try:
            batch_op.drop_index("ix_plans_session_start")
        except Exception:
            pass
    op.drop_table("plans")

    try:
        op.drop_index("ix_sessions_user_id", table_name="sessions")
    except Exception:
        pass
    op.drop_table("sessions")
