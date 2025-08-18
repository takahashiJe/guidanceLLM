# -*- coding: utf-8 -*-
"""
DB 初期化スクリプト（db-init コンテナ用）
------------------------------------------------------------
役割:
  1) Postgres の起動を待つ（リトライ）
  2) Alembic によりスキーマを最新化（upgrade head）
  3) 初回起動直後に、存在するマテビューをリフレッシュ
     - congestion_by_date_spot（ユニークインデックスがあれば CONCURRENTLY）
     - spot_congestion_mv（存在する場合の後方互換）

環境変数:
  - DATABASE_URL / ALEMBIC_DB_URL: DB 接続 URL（どちらかがあれば可）
  - ALEMBIC_SCRIPT_LOCATION: Alembic の migrations ディレクトリ
      例: backend/shared/app/migrations
      未指定の場合は __file__ から相対で解決
  - ALEMBIC_INI_PATH: alembic.ini のパス（既定 "alembic.ini"）
  - DB_INIT_MAX_WAIT_SEC: DB 起動待機の最大秒数（既定 120）
  - DB_INIT_RETRY_INTERVAL_SEC: 接続リトライ間隔（既定 2）
"""

from __future__ import annotations

import os
import sys
import time
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from alembic import command
from alembic.config import Config


# ------------------------------------------------------------
# ロガー設定
# ------------------------------------------------------------
logger = logging.getLogger("db-init")
handler = logging.StreamHandler(stream=sys.stdout)
formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


# ------------------------------------------------------------
# ユーティリティ
# ------------------------------------------------------------
def _resolve_db_url() -> str:
    """DATABASE_URL / ALEMBIC_DB_URL のいずれかから DB URL を決定。"""
    db_url = os.getenv("ALEMBIC_DB_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL または ALEMBIC_DB_URL が未設定です。")
    return db_url


def _resolve_script_location() -> str:
    """
    Alembic の migrations ディレクトリを特定。
    環境変数がなければ、__file__ から:
      backend/worker/app/init_db_script.py
      -> backend/shared/app/migrations
    """
    env_loc = os.getenv("ALEMBIC_SCRIPT_LOCATION")
    if env_loc:
        return env_loc

    here = Path(__file__).resolve()
    backend_dir = here.parents[2]  # .../backend
    migrations = backend_dir / "shared" / "app" / "migrations"
    return str(migrations)


def _wait_for_db(db_url: str, max_wait: int = 120, interval: int = 2) -> Engine:
    """Postgres 起動待機（接続が開くまでリトライ）。"""
    engine = create_engine(db_url, future=True)
    start = time.time()
    while True:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("DB 接続に成功しました。")
            return engine
        except Exception as e:
            elapsed = int(time.time() - start)
            if elapsed >= max_wait:
                logger.error("DB 接続待機のタイムアウト: %s", str(e))
                raise
            logger.info("DB 起動待機中... (%ds/%ds): %s", elapsed, max_wait, str(e))
            time.sleep(interval)


def _run_alembic_upgrade(script_location: str, db_url: str) -> None:
    """Alembic で upgrade head を実行。"""
    ini_path = os.getenv("ALEMBIC_INI_PATH", "alembic.ini")
    logger.info("Alembic 実行開始: script_location=%s, ini=%s", script_location, ini_path)

    cfg = Config(ini_path)
    # ini 側の設定を環境変数で上書き
    cfg.set_main_option("script_location", script_location)
    cfg.set_main_option("sqlalchemy.url", db_url)

    # upgrade 実行
    command.upgrade(cfg, "head")
    logger.info("Alembic upgrade head 完了。")


def _refresh_materialized_view(engine: Engine, name: str, use_concurrently: bool = True) -> None:
    """
    マテリアライズドビューを REFRESH。
    - CONCURRENTLY が使えない場合（初回など）は通常 REFRESH にフォールバック。
    """
    with engine.begin() as conn:
        if use_concurrently:
            try:
                conn.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {name};"))
                logger.info("REFRESH MATERIALIZED VIEW CONCURRENTLY %s 実行。", name)
                return
            except Exception as e:
                logger.warning("CONCURRENTLY 失敗のため通常 REFRESH にフォールバック: %s", e)

        # 通常 REFRESH
        try:
            conn.execute(text(f"REFRESH MATERIALIZED VIEW {name};"))
            logger.info("REFRESH MATERIALIZED VIEW %s 実行。", name)
        except Exception as e:
            # MV が存在しない等は警告ログのみ（冪等運用のため）
            logger.warning("REFRESH MATERIALIZED VIEW %s 失敗（スキップ）: %s", name, e)


def main() -> int:
    try:
        db_url = _resolve_db_url()
        max_wait = int(os.getenv("DB_INIT_MAX_WAIT_SEC", "120"))
        interval = int(os.getenv("DB_INIT_RETRY_INTERVAL_SEC", "2"))

        # 1) DB 起動待機
        engine = _wait_for_db(db_url, max_wait=max_wait, interval=interval)

        # 2) Alembic upgrade head
        script_location = _resolve_script_location()
        _run_alembic_upgrade(script_location, db_url)

        # 3) マテビューの初期 REFRESH（存在すれば）
        #    - フェーズ5で合意した集計 MV
        _refresh_materialized_view(engine, "congestion_by_date_spot", use_concurrently=True)
        #    - 既存互換（もし使っていれば）
        _refresh_materialized_view(engine, "spot_congestion_mv", use_concurrently=True)

        logger.info("DB 初期化シーケンス完了。")
        return 0

    except Exception as e:
        logger.exception("DB 初期化シーケンスで致命的エラー: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
