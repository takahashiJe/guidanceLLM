# -*- coding: utf-8 -*-
"""
DB 初期化スクリプト（コンテナ起動時に db-init で実行）
- マイグレーション（Alembic）を先に実施（INIT_RUN_ALEMBIC=true のとき）
- Spots / AccessPoints のロード（ON CONFLICT DO NOTHING/UPDATE で冪等）
- Vectorstore（RAG）の構築（INIT_BUILD_VECTORSTORE=true のとき）
  - RAG_REBUILD_IF_EMPTY_ONLY=true: 既存が空の場合のみ構築
"""
import os
import json
import logging
from pathlib import Path
from typing import List, Dict, Any

from sqlalchemy.orm import Session, create_engine, text

from shared.app.database import SessionLocal, engine
from shared.app.models import Spot, AccessPoint
from shared.app.database import Base

# Alembic を Python API から実行
from alembic.config import Config as AlembicConfig
from alembic import command as alembic_command

logger = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

# 環境変数
INIT_RUN_ALEMBIC = os.getenv("INIT_RUN_ALEMBIC", "true").lower() == "true"
INIT_LOAD_SPOTS = os.getenv("INIT_LOAD_SPOTS", "true").lower() == "true"
INIT_LOAD_ACCESS_POINTS = os.getenv("INIT_LOAD_ACCESS_POINTS", "true").lower() == "true"
INIT_BUILD_VECTORSTORE = os.getenv("INIT_BUILD_VECTORSTORE", "true").lower() == "true"

ALEMBIC_INI = os.getenv("ALEMBIC_INI", "alembic.ini")
ALEMBIC_SCRIPT_LOCATION = os.getenv("ALEMBIC_SCRIPT_LOCATION", "backend/shared/app/migrations")
ALEMBIC_DB_URL = os.getenv("ALEMBIC_DB_URL") or os.getenv("DATABASE_URL")

KNOWLEDGE_BASE_DIR = Path(os.getenv("KNOWLEDGE_BASE_DIR", "/app/worker/app/data/knowledge/ja"))
VECTORSTORE_DIR = Path(os.getenv("VECTORSTORE_DIR", "/app/worker/app/data/vectorstore/ja"))
RAG_BATCH_SIZE = int(os.getenv("RAG_BATCH_SIZE", "50"))
RAG_REBUILD_IF_EMPTY_ONLY = os.getenv("RAG_REBUILD_IF_EMPTY_ONLY", "true").lower() == "true"

SPOTS_JSON = Path("/app/worker/app/data/POI.json")
ACCESS_POINTS_GEOJSON = Path("/app/scripts/access_points.geojson")
DB_URL = os.getenv("DATABASE_URL")  # 例: postgresql+psycopg2://user:pass@db:5432/app
engine = create_engine(DB_URL, future=True)

MV_SQL = """
CREATE MATERIALIZED VIEW IF NOT EXISTS congestion_by_date_spot AS
SELECT
  s.spot_id AS spot_id,
  p.start_date::date AS visit_date,
  COUNT(DISTINCT p.user_id) AS user_count
FROM plans p
JOIN stops s ON s.plan_id = p.id
GROUP BY s.spot_id, p.start_date
WITH NO DATA;
"""

# CONCURRENTLY リフレッシュにはユニークインデックスが必須
MV_UNIQUE_IDX_SQL = """
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes WHERE indexname = 'uq_congestion_by_date_spot'
  ) THEN
    CREATE UNIQUE INDEX uq_congestion_by_date_spot
      ON congestion_by_date_spot(spot_id, visit_date);
  END IF;
END $$;
"""


# ---------- ユーティリティ ----------

def run_alembic_upgrade_head():
    """Alembic マイグレーションを head まで適用（冪等）"""
    logger.info("Running Alembic migrations...")
    alembic_cfg = AlembicConfig(ALEMBIC_INI)
    alembic_cfg.set_main_option("script_location", ALEMBIC_SCRIPT_LOCATION)
    if ALEMBIC_DB_URL:
        alembic_cfg.set_main_option("sqlalchemy.url", ALEMBIC_DB_URL)
    alembic_command.upgrade(alembic_cfg, "head")
    logger.info("Alembic upgrade head done.")


def load_spots(db: Session, data: List[Dict[str, Any]]):
    """Spots を UPSERT でロード（official_name をユニークキー相当とみなす）"""
    # official_name のユニーク制約がない場合でも、アプリ上で同一名は同一スポットという運用を想定
    for rec in data:
        official_name = rec.get("official_name")
        if not official_name:
            continue
        existing = db.query(Spot).filter(Spot.official_name == official_name).one_or_none()
        if existing:
            # 更新フィールドを最小限に（タグや座標など）
            existing.lat = rec.get("lat", existing.lat)
            existing.lon = rec.get("lon", existing.lon)
            existing.tags = rec.get("tags", existing.tags)
            existing.spot_type = rec.get("spot_type", existing.spot_type)
            existing.description = rec.get("description", existing.description)
            existing.social_proof = rec.get("social_proof", existing.social_proof)
        else:
            db.add(Spot(**rec))
    db.commit()


def load_access_points(db: Session, features: List[Dict[str, Any]]):
    """AccessPoints を UPSERT でロード（name + coords をキー相当で同一判定）"""
    for f in features:
        props = f.get("properties", {}) or {}
        geom = f.get("geometry", {}) or {}
        coords = geom.get("coordinates", [])
        if len(coords) != 2:
            continue
        lon, lat = coords
        name = props.get("name") or props.get("title") or "Unnamed"

        existing = (
            db.query(AccessPoint)
            .filter(AccessPoint.name == name, AccessPoint.lat == lat, AccessPoint.lon == lon)
            .one_or_none()
        )
        if existing:
            existing.ap_type = props.get("ap_type", existing.ap_type)
            existing.tags = props.get("tags", existing.tags)
        else:
            db.add(
                AccessPoint(
                    name=name,
                    lat=lat,
                    lon=lon,
                    ap_type=props.get("ap_type", "unknown"),
                    tags=props.get("tags"),
                )
            )
    db.commit()


def build_vectorstore_if_needed():
    """Vectorstore 構築（Chroma など）。冪等ポリシーに基づき実行。"""
    from pathlib import Path
    import glob

    # 既存が空でない場合のスキップ
    if RAG_REBUILD_IF_EMPTY_ONLY and VECTORSTORE_DIR.exists():
        # chroma.sqlite3 が存在し、かつそれっぽいインデックスがあるならスキップ
        sqlite_path = VECTORSTORE_DIR / "chroma.sqlite3"
        if sqlite_path.exists():
            logger.info("Vectorstore already exists. Skip build (RAG_REBUILD_IF_EMPTY_ONLY=true).")
            return

    # ここで実際の取り込み（01_build_knowledge_graph.py のロジックを流用/呼び出し）
    # 直接 import して関数呼び出しでもOKだが、スクリプトをそのまま使えるようにロジックを内蔵
    try:
        from sentence_transformers import SentenceTransformer
        import chromadb

        logger.info("Building vectorstore...")
        VECTORSTORE_DIR.mkdir(parents=True, exist_ok=True)

        # コレクション名は言語で分ける
        collection_name = f"knowledge_{os.getenv('KNOWLEDGE_LANG','ja')}"
        client = chromadb.PersistentClient(path=str(VECTORSTORE_DIR))
        collection = client.get_or_create_collection(name=collection_name)

        # すでにドキュメントが入っていればスキップ
        if RAG_REBUILD_IF_EMPTY_ONLY and collection.count() > 0:
            logger.info("Vectorstore collection already has embeddings. Skip.")
            return

        model = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

        # MD を再帰的に走査
        md_files = list(Path(KNOWLEDGE_BASE_DIR).rglob("*.md"))
        logger.info(f"Found {len(md_files)} markdown files under {KNOWLEDGE_BASE_DIR}")

        docs, ids, metas = [], [], []
        for md in md_files:
            text = md.read_text(encoding="utf-8", errors="ignore")
            if not text.strip():
                continue
            docs.append(text)
            ids.append(md.as_posix())  # ファイルパスを一意IDに
            metas.append({"path": md.as_posix()})

            if len(docs) >= RAG_BATCH_SIZE:
                embeddings = model.encode(docs, normalize_embeddings=True).tolist()
                collection.upsert(documents=docs, ids=ids, metadatas=metas, embeddings=embeddings)
                docs, ids, metas = [], [], []

        if docs:
            embeddings = model.encode(docs, normalize_embeddings=True).tolist()
            collection.upsert(documents=docs, ids=ids, metadatas=metas, embeddings=embeddings)

        logger.info("Vectorstore build completed.")
    except Exception as e:
        logger.exception("Vectorstore build failed: %s", e)

def create_materialized_view():
    # 既存有無チェックしつつ作成（冪等）
    create_sql = """
    CREATE MATERIALIZED VIEW IF NOT EXISTS spot_congestion_mv
    AS
    SELECT p.start_date AS visit_date,
           s.spot_id    AS spot_id,
           COUNT(DISTINCT p.id) AS plan_count
    FROM plans p
    JOIN stops s ON s.plan_id = p.id
    WHERE p.start_date IS NOT NULL
    GROUP BY p.start_date, s.spot_id
    WITH NO DATA;
    """
    index_sql = """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_spot_congestion_mv_unique
      ON spot_congestion_mv (visit_date, spot_id);
    """
    refresh_sql = "REFRESH MATERIALIZED VIEW CONCURRENTLY spot_congestion_mv;"

    with engine.begin() as conn:
        conn.execute(text(create_sql))
        conn.execute(text(index_sql))
        # 初回のみ CONCURRENTLY は使えないため try/except で通常 REFRESH にフォールバック
        try:
            conn.execute(text(refresh_sql))
        except Exception:
            conn.execute(text("REFRESH MATERIALIZED VIEW spot_congestion_mv;"))

def main():
    logger.info("=== DB init started ===")

    # 1) Alembic
    if INIT_RUN_ALEMBIC:
        run_alembic_upgrade_head()

    # 2) DB ロード
    db = SessionLocal()
    try:
        if INIT_LOAD_SPOTS and SPOTS_JSON.exists():
            logger.info("Loading spots from %s ...", SPOTS_JSON)
            data = json.loads(SPOTS_JSON.read_text(encoding="utf-8"))
            if isinstance(data, list):
                load_spots(db, data)
            else:
                logger.warning("POI.json is not a list. Skip.")

        if INIT_LOAD_ACCESS_POINTS and ACCESS_POINTS_GEOJSON.exists():
            logger.info("Loading access points from %s ...", ACCESS_POINTS_GEOJSON)
            geo = json.loads(ACCESS_POINTS_GEOJSON.read_text(encoding="utf-8"))
            features = geo.get("features", [])
            load_access_points(db, features)
    finally:
        db.close()

    # 3) Vectorstore
    if INIT_BUILD_VECTORSTORE:
        build_vectorstore_if_needed()

    logger.info("=== DB init completed ===")
    create_materialized_view()

    with engine.connect() as conn:
        conn.execute(text(MV_SQL))
        conn.execute(text(MV_UNIQUE_IDX_SQL))
        conn.commit()


if __name__ == "__main__":
    main()