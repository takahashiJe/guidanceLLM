# -*- coding: utf-8 -*-
# pre_generated_guides（親テーブルが無くても適用できる条件付きFK）
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_pre_guides"
down_revision = "0001_ap_geom"
branch_labels = None
depends_on = None

TABLE = "pre_generated_guides"
UQ_NAME = "uq_pre_guides_session_spot_lang"
IX_NAME = "ix_pre_guides_spot_lang"
FK_SESS = "fk_pre_guides_session_id"
FK_SPOT = "fk_pre_guides_spot_id"

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

def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    has_sessions = _has_table(inspector, "sessions")
    has_spots    = _has_table(inspector, "spots")

    # 1) 親がなくても作れるよう、まずはプレーンに作成
    if TABLE not in inspector.get_table_names():
        op.create_table(
            TABLE,
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True, nullable=False),
            sa.Column("session_id", sa.String(length=64), nullable=False, index=True),
            sa.Column("spot_id", sa.Integer, nullable=False, index=True),
            sa.Column("lang", sa.String(length=8), nullable=False, index=True),
            sa.Column("text", sa.Text, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )

    # 2) 一意 & 併用インデックス
    if UQ_NAME not in [c["name"] for c in inspector.get_unique_constraints(TABLE)]:
        op.create_unique_constraint(UQ_NAME, TABLE, ["session_id", "spot_id", "lang"])
    if IX_NAME not in [ix["name"] for ix in inspector.get_indexes(TABLE)]:
        op.create_index(IX_NAME, TABLE, ["spot_id", "lang"], unique=False)

    # 3) 親が存在するなら FK を後付け
    if has_sessions and not _has_fk(bind, TABLE, FK_SESS):
        op.create_foreign_key(FK_SESS, TABLE, "sessions", ["session_id"], ["id"], ondelete="CASCADE")
    if has_spots and not _has_fk(bind, TABLE, FK_SPOT):
        op.create_foreign_key(FK_SPOT, TABLE, "spots", ["spot_id"], ["id"], ondelete="CASCADE")

def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # FK を先に落とす（存在チェック付き）
    try:
        if _has_fk(bind, TABLE, FK_SPOT):
            op.drop_constraint(FK_SPOT, TABLE, type_="foreignkey")
    except Exception:
        pass
    try:
        if _has_fk(bind, TABLE, FK_SESS):
            op.drop_constraint(FK_SESS, TABLE, type_="foreignkey")
    except Exception:
        pass

    # インデックス/一意
    try:
        if IX_NAME in [ix["name"] for ix in inspector.get_indexes(TABLE)]:
            op.drop_index(IX_NAME, table_name=TABLE)
    except Exception:
        pass
    try:
        if UQ_NAME in [c["name"] for c in inspector.get_unique_constraints(TABLE)]:
            op.drop_constraint(UQ_NAME, TABLE, type_="unique")
    except Exception:
        pass

    if TABLE in inspector.get_table_names():
        op.drop_table(TABLE)
