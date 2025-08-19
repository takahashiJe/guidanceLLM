# -*- coding: utf-8 -*-
# access_points.geom の重複 GiST インデックスを掃除（任意）
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007_cleanup_ap_geom_index"
down_revision = "0006_create_mv"
branch_labels = None
depends_on = None

TABLE = "access_points"
KEEP = "ix_access_points_geom"
DROP = "idx_access_points_geom"  # ← こちらを削除対象とする

def _has_index(inspector, table: str, name: str) -> bool:
    return any(ix["name"] == name for ix in inspector.get_indexes(table))

def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_index(inspector, TABLE, KEEP) and _has_index(inspector, TABLE, DROP):
        try:
            op.drop_index(DROP, table_name=TABLE)
        except Exception:
            pass  # 環境差異でもコケないよう冗長に

def downgrade() -> None:
    # 再作成は省略（性能上のメリットが無いため）。必要であればここで CREATE INDEX を再現してください。
    pass
