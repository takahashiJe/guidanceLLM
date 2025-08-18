# backend/shared/app/migrations/versions/20250815_01_add_pre_generated_guides.py
# -----------------------------------------------------------------------------
# 目的:
#   pre_generated_guides テーブルを追加
#   - session_id + spot_id + lang のユニーク制約
#   - spot_id は spots.id に外部キー（存在する場合）
# 備考:
#   既存環境に安全に適用できるよう IF NOT EXISTS 相当を考慮。
# -----------------------------------------------------------------------------

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as psql

# リビジョン識別子
revision = "20250815_01_add_pre_generated_guides"
down_revision = None
branch_labels = None
depends_on = None

TABLE_NAME = "pre_generated_guides"

def upgrade() -> None:
    # すでに存在する場合はスキップ（マニュアル運用に備えた冪等性）
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE_NAME in inspector.get_table_names():
        # 既に存在する場合でも、ユニーク制約がなければ追加を試みる
        existing_uqs = {uc["name"] for uc in inspector.get_unique_constraints(TABLE_NAME)}
        if "uq_pre_generated_guides_session_spot_lang" not in existing_uqs:
            op.create_unique_constraint(
                "uq_pre_generated_guides_session_spot_lang",
                TABLE_NAME,
                ["session_id", "spot_id", "lang"],
            )
        return

    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer, primary_key=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("spot_id", sa.Integer, nullable=False, index=True),
        sa.Column("lang", sa.String(length=8), nullable=False, index=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        # 外部キー（spots.id）: 既存の spots テーブルに依存
        sa.ForeignKeyConstraint(["spot_id"], ["spots.id"], name="fk_pre_generated_guides_spot_id", ondelete="CASCADE"),
    )

    op.create_unique_constraint(
        "uq_pre_generated_guides_session_spot_lang",
        TABLE_NAME,
        ["session_id", "spot_id", "lang"],
    )

def downgrade() -> None:
    # テーブルが存在する場合のみ削除
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE_NAME in inspector.get_table_names():
        op.drop_constraint("uq_pre_generated_guides_session_spot_lang", TABLE_NAME, type_="unique")
        op.drop_table(TABLE_NAME)
