# backend/worker/app/init_db_script.py
# ------------------------------------------------------------
# 役割:
#  - DB 起動待ち
#  - pgvector 拡張 (可能なら) の作成
#  - Base.metadata.create_all() によるテーブル作成
#  - マテビュー:
#      * congestion_by_date_spot   （フェーズ2で導入）
#      * spot_congestion_mv        （既存タスク refresh_spot_congestion_mv が参照）
#    の冪等作成 + ユニークインデックス作成
#  - conversation_embeddings の KNN インデックス（pgvector 環境のみ; 失敗は無視）
# すべて冪等に実行し、既存の初期化内容を壊さない。
# ------------------------------------------------------------
import os
import time
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from shared.app.models import Base

DB_URL = os.getenv("DATABASE_URL")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))  # mxbai-embed-large = 1024


def wait_for_db():
    """PostgreSQL が応答するまで待機。"""
    if not DB_URL:
        raise RuntimeError("DATABASE_URL is not set")
    engine = create_engine(DB_URL, future=True)
    for _ in range(60):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                return engine
        except OperationalError:
            time.sleep(2)
    raise RuntimeError("DB is not ready after waiting.")


def ensure_extensions(engine):
    """pgvector 拡張を可能なら作成（未導入環境でもエラーにせず続行）。"""
    with engine.begin() as conn:
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        except Exception as e:
            # ベアの Postgres イメージ等で拡張が無い場合は通知のみ
            print(f"[init_db_script] NOTE: pgvector extension not available: {e}")


def create_tables(engine):
    """SQLAlchemy モデルに基づくテーブルを作成（冪等）。"""
    Base.metadata.create_all(bind=engine)


def create_materialized_views(engine):
    """
    マテビュー2種を冪等作成 + ユニークインデックス作成。
    - congestion_by_date_spot  : (visit_date, spot_id) ごとの distinct user_count
    - spot_congestion_mv       : 同等定義（既存タスク互換のため保持）
    """
    with engine.begin() as conn:
        # 1) congestion_by_date_spot
        conn.execute(text("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS congestion_by_date_spot AS
        SELECT
            p.start_date AS visit_date,
            s.spot_id,
            COUNT(DISTINCT p.user_id) AS user_count
        FROM plans p
        JOIN stops s ON s.plan_id = p.id
        WHERE p.start_date IS NOT NULL
        GROUP BY p.start_date, s.spot_id;
        """))

        # ユニークインデックス（CONCURRENTLY はここでは使わない＝トランザクション内でOK）
        conn.execute(text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_class WHERE relname = 'uq_congestion_by_date_spot'
            ) THEN
                CREATE UNIQUE INDEX uq_congestion_by_date_spot
                ON congestion_by_date_spot (visit_date, spot_id);
            END IF;
        END
        $$;
        """))

        # 2) spot_congestion_mv （既存タスク refresh_spot_congestion_mv が参照する名称）
        conn.execute(text("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS spot_congestion_mv AS
        SELECT
            p.start_date AS visit_date,
            s.spot_id,
            COUNT(DISTINCT p.user_id) AS user_count
        FROM plans p
        JOIN stops s ON s.plan_id = p.id
        WHERE p.start_date IS NOT NULL
        GROUP BY p.start_date, s.spot_id;
        """))

        conn.execute(text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_class WHERE relname = 'uq_spot_congestion_mv'
            ) THEN
                CREATE UNIQUE INDEX uq_spot_congestion_mv
                ON spot_congestion_mv (visit_date, spot_id);
            END IF;
        END
        $$;
        """))


def create_knn_index_if_possible(engine):
    """
    conversation_embeddings の pgvector KNN インデックス（IVFFLAT）を冪等作成。
    - pgvector 未導入/列型が vector でない場合は失敗するため、例外は握りつぶして通知のみ。
    """
    with engine.begin() as conn:
        try:
            conn.execute(text(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_class WHERE relname = 'ix_convemb_embedding_ivfflat'
                ) THEN
                    CREATE INDEX ix_convemb_embedding_ivfflat
                    ON conversation_embeddings
                    USING ivfflat (embedding vector_l2_ops) WITH (lists = 100);
                END IF;
            END
            $$;
            """))
        except Exception as e:
            print(f"[init_db_script] NOTE: skip IVFFLAT index (pgvector/column mismatch?): {e}")


def main():
    engine = wait_for_db()
    ensure_extensions(engine)       # 既存に追加（安全）
    create_tables(engine)           # 既存：全テーブル作成
    create_materialized_views(engine)  # 既存/新規どちらも網羅
    create_knn_index_if_possible(engine)  # 追加（pgvector 環境のみ）
    print("[init_db_script] DB init completed.")


if __name__ == "__main__":
    main()
