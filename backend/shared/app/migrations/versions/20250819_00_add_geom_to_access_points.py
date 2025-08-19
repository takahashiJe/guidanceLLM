# -*- coding: utf-8 -*-
# 20250819_00_add_geom_to_access_points.py
# access_points に geom(Point,4326) と GiST を追加 / (latitude, longitude) の一意制約を保証
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

try:
    from geoalchemy2 import Geometry
    GEOM_TYPE = Geometry(geometry_type="POINT", srid=4326)
except Exception:
    # なくても動くようにフォールバック（型はSQL側で解決）
    GEOM_TYPE = sa.types.UserDefinedType()

# 短いID（<=32文字目安）
revision = "0001_ap_geom"
down_revision = None  # 既存履歴がある場合は直前IDに差し替えてください
branch_labels = None
depends_on = None

TABLE = "access_points"
GEOM_COL = "geom"
GIST_INDEX = "ix_access_points_geom"
UNIQ_NAME = "uq_access_points_lat_lon"

def _has_table(inspector: Inspector, table: str) -> bool:
    return table in inspector.get_table_names()

def _has_column(inspector: Inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspector.get_columns(table))

def _has_index(inspector: Inspector, table: str, name: str) -> bool:
    return any(ix["name"] == name for ix in inspector.get_indexes(table))

def _has_unique(inspector: Inspector, table: str, name: str) -> bool:
    return any(uq["name"] == name for uq in inspector.get_unique_constraints(table))

def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # --- 1) PostGIS 拡張の安全な適用 ---------------------------------------
    # 事前に"利用可能"かチェック（パッケージ未導入なら行が返らない）
    available = conn.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name='postgis'")
    ).scalar()
    if available:
        # 失敗してもトランザクション全体を壊さないよう SAVEPOINT で囲む
        try:
            op.execute(sa.text("SAVEPOINT sp_postgis"))
            op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS postgis"))
            op.execute(sa.text("RELEASE SAVEPOINT sp_postgis"))
        except Exception:
            # 拡張作成に失敗（権限等）。ロールバックして以降のDDLを続行可能にする
            op.execute(sa.text("ROLLBACK TO SAVEPOINT sp_postgis"))
    # ----------------------------------------------------------------------

    # --- 2) access_points テーブルが無ければ何もしない（後続マイグレで作成される想定ならスキップ） ---
    if not _has_table(inspector, TABLE):
        return

    # 最新の情報で再取得（拡張作成の影響を避けるため）
    inspector = sa.inspect(conn)

    # --- 3) geom 列追加 & 既存行を埋める -----------------------------------
    if not _has_column(inspector, TABLE, GEOM_COL):
        op.add_column(TABLE, sa.Column(GEOM_COL, GEOM_TYPE, nullable=True))
        # 既存行の geom を (lon,lat) から生成
        op.execute(sa.text(
            f"UPDATE {TABLE} "
            f"SET {GEOM_COL} = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) "
            f"WHERE {GEOM_COL} IS NULL"
        ))

    # --- 4) (latitude, longitude) の一意制約 --------------------------------
    inspector = sa.inspect(conn)
    if not _has_unique(inspector, TABLE, UNIQ_NAME):
        op.create_unique_constraint(UNIQ_NAME, TABLE, ["latitude", "longitude"])

    # --- 5) GiST インデックス（geom） --------------------------------------
    inspector = sa.inspect(conn)
    if not _has_index(inspector, TABLE, GIST_INDEX):
        op.create_index(GIST_INDEX, TABLE, [GEOM_COL], unique=False, postgresql_using="gist")


def downgrade() -> None:
    # 逆順で落とす
    try:
        op.drop_index(GIST_INDEX, table_name=TABLE)
    except Exception:
        pass
    try:
        op.drop_constraint(UNIQ_NAME, TABLE, type_="unique")
    except Exception:
        pass
    try:
        op.drop_column(TABLE, GEOM_COL)
    except Exception:
        pass
