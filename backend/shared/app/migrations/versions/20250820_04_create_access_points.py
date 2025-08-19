# -*- coding: utf-8 -*-
# access_points を新規作成（なければ）
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

try:
    from geoalchemy2 import Geometry
    GEOM_TYPE = Geometry(geometry_type="POINT", srid=4326)
except Exception:
    GEOM_TYPE = sa.types.UserDefinedType()

revision = "0004_ap_table"
down_revision = "0003_embeddings_mv"
branch_labels = None
depends_on = None

TABLE = "access_points"
GEOM_COL = "geom"
GIST_INDEX = "ix_access_points_geom"
UNIQ_NAME = "uq_access_points_lat_lon"

def _has_table(inspector: Inspector, table: str) -> bool:
    return table in inspector.get_table_names()

def _has_index(inspector: Inspector, table: str, name: str) -> bool:
    return any(ix["name"] == name for ix in inspector.get_indexes(table))

def _has_unique(inspector: Inspector, table: str, name: str) -> bool:
    return any(uq["name"] == name for uq in inspector.get_unique_constraints(table))

def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # PostGIS 拡張（利用可能なら）
    available = conn.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name='postgis'")
    ).scalar()
    if available:
        try:
            op.execute(sa.text("SAVEPOINT sp_postgis"))
            op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS postgis"))
            op.execute(sa.text("RELEASE SAVEPOINT sp_postgis"))
        except Exception:
            op.execute(sa.text("ROLLBACK TO SAVEPOINT sp_postgis"))

    # ap_type ENUM（無ければ作成）
    op.execute(sa.text("""
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'ap_type') THEN
            CREATE TYPE ap_type AS ENUM ('parking', 'trailhead', 'other');
        END IF;
    END$$;
    """))

    # access_points が無ければ作成
    if not _has_table(inspector, TABLE):
        op.create_table(
            TABLE,
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True, nullable=False),
            sa.Column("name", sa.String(length=128), nullable=True),
            sa.Column("ap_type", sa.dialects.postgresql.ENUM(
                "parking", "trailhead", "other", name="ap_type", create_type=False
            ), nullable=False, server_default=sa.text("'other'")),
            sa.Column("latitude", sa.Float, nullable=False),
            sa.Column("longitude", sa.Float, nullable=False),
            sa.Column(GEOM_COL, GEOM_TYPE, nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        # 既存行は無い想定だが、保険として geom を満たす
        op.execute(sa.text(
            f"UPDATE {TABLE} "
            f"SET {GEOM_COL} = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) "
            f"WHERE {GEOM_COL} IS NULL"
        ))

    # 一意制約 (lat,lon)
    inspector = sa.inspect(conn)
    if not _has_unique(inspector, TABLE, UNIQ_NAME):
        op.create_unique_constraint(UNIQ_NAME, TABLE, ["latitude", "longitude"])

    # GiST index on geom
    inspector = sa.inspect(conn)
    if not _has_index(inspector, TABLE, GIST_INDEX):
        op.create_index(GIST_INDEX, TABLE, [GEOM_COL], unique=False, postgresql_using="gist")


def downgrade() -> None:
    # 逆順で安全に削除
    try:
        op.drop_index(GIST_INDEX, table_name=TABLE)
    except Exception:
        pass
    try:
        op.drop_constraint(UNIQ_NAME, TABLE, type_="unique")
    except Exception:
        pass
    try:
        op.drop_table(TABLE)
    except Exception:
        pass
    # ap_type 型は他で使っていなければ削除
    op.execute(sa.text("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_attribute a
            JOIN pg_type t ON a.atttypid = t.oid
            WHERE t.typname = 'ap_type'
        ) THEN
            DROP TYPE IF EXISTS ap_type;
        END IF;
    END$$;
    """))
