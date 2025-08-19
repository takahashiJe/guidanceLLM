# -*- coding: utf-8 -*-
"""Create conversation_history table and its index to match models.py

Revision ID: 0012_conversation_history
Revises: 0011_align_models
Create Date: 2025-08-21 00:05:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_conversation_history"
down_revision = "0011_align_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tables = inspector.get_table_names()
    if "conversation_history" not in tables:
        op.create_table(
            "conversation_history",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
            sa.Column("session_id", sa.String(length=64), nullable=False, index=True),
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("lang", sa.String(length=8), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        if "sessions" in tables:
            op.create_foreign_key(
                "fk_convhist_session_id_sessions",
                source_table="conversation_history",
                referent_table="sessions",
                local_cols=["session_id"],
                remote_cols=["id"],
                ondelete="CASCADE",
            )

    existing = [ix.get("name") for ix in inspector.get_indexes("conversation_history")] if "conversation_history" in tables else []
    if "ix_convhist_session_created" not in existing and "conversation_history" in tables:
        op.create_index("ix_convhist_session_created", "conversation_history", ["session_id", "created_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "conversation_history" in inspector.get_table_names():
        try:
            op.drop_index("ix_convhist_session_created", table_name="conversation_history")
        except Exception:
            pass
        op.drop_table("conversation_history")
