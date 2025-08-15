# -*- coding: utf-8 -*-
"""
pre_generated_guides テーブルの追加

- セッション開始時に事前生成したガイド文を保存するための永続層
- （session_id, spot_id, lang）にユニーク制約
"""
from alembic import op
import sqlalchemy as sa

# リビジョン識別子
revision = "20250815_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pre_generated_guides",
        sa.Column("id", sa.Integer, primary_key=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("spot_id", sa.Integer, nullable=False, index=True),
        sa.Column("lang", sa.String(length=8), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint(
        "uq_pre_generated_guides_session_spot_lang",
        "pre_generated_guides",
        ["session_id", "spot_id", "lang"],
    )


def downgrade():
    op.drop_constraint("uq_pre_generated_guides_session_spot_lang", "pre_generated_guides", type_="unique")
    op.drop_table("pre_generated_guides")
