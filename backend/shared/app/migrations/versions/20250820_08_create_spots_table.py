# -*- coding: utf-8 -*-
# spots テーブルを新規作成（なければ）+ geom(Point,4326) と GiST を付与
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

try:
    from geoalchemy2 import Geometry
    GEOM_TYPE = Geometry(geometry_type="POINT", srid=4326)
except Exception:
    GEOM_TYPE = sa.types.UserDefinedType()

from sqlalchemy.dialects.postgresql import JSONB

revision = "0008_create_spots_table"
down_revision = "0007_cleanup_ap_geom_index"
branch_labels = None
depends_on = None

TABLE = "spots"
GEOM_COL = "geom"
GIST_INDEX = "ix_spots_geom"
UNIQ_LATLON = "uq_spots_lat_lon"

def _has_table(inspector, name: str) -> bool:
    return name in inspector.get_table_names()

def _has_index(inspector, table: str, name: str) -> bool:
    return any(ix["name"] == name for ix in inspector.get_indexes(table))

def _has_unique(inspector, table: str, name: str) -> bool:
    return any(uq["name"] == name for uq in inspector.get_unique_constraints(table))

def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # PostGIS 拡張（利用可能なら）
    available = bind.execute(sa.text(
        "SELECT 1 FROM pg_available_extensions WHERE name='postgis'"
    )).scalar()
    if available:
        try:
            op.execute(sa.text("SAVEPOINT sp_postgis"))
            op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS postgis"))
            op.execute(sa.text("RELEASE SAVEPOINT sp_postgis"))
        except Exception:
            op.execute(sa.text("ROLLBACK TO SAVEPOINT sp_postgis"))

    if not _has_table(inspector, TABLE):
        op.create_table(
            TABLE,
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True, nullable=False),
            sa.Column("official_name", sa.String(length=256), nullable=True),
            sa.Column("spot_type", sa.String(length=64), nullable=True),  # ローダが文字列を入れる想定に合わせる
            sa.Column("tags", JSONB, nullable=True),
            sa.Column("latitude", sa.Float, nullable=False),
            sa.Column("longitude", sa.Float, nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("social_proof", JSONB, nullable=True),
            sa.Column(GEOM_COL, GEOM_TYPE, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )

        # 既存行（この段階では無い想定）も含め geom を埋める
        op.execute(sa.text(
            f"UPDATE {TABLE} "
            f"SET {GEOM_COL} = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) "
            f"WHERE {GEOM_COL} IS NULL"
        ))

    # (lat,lon) 一意 — ローダが位置で upsert する/重複防止のため
    inspector = sa.inspect(bind)
    if not _has_unique(inspector, TABLE, UNIQ_LATLON):
        op.create_unique_constraint(UNIQ_LATLON, TABLE, ["latitude", "longitude"])

    # GiST index on geom（KNN検索用）
    inspector = sa.inspect(bind)
    if not _has_index(inspector, TABLE, GIST_INDEX):
        op.create_index(GIST_INDEX, TABLE, [GEOM_COL], unique=False, postgresql_using="gist")


def downgrade() -> None:
    # 逆順で安全に削除
    try:
        op.drop_index(GIST_INDEX, table_name=TABLE)
    except Exception:
        pass
    try:
        op.drop_constraint(UNIQ_LATLON, TABLE, type_="unique")
    except Exception:
        pass
    try:
        op.drop_table(TABLE)
    except Exception:
        pass
