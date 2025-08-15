# -*- coding: utf-8 -*-
"""
共有Celeryタスク定義。
- ここにマテビュー更新タスクを追加
- 既存のタスク名・インポートは壊さない（orchestrate, STT/TTS, routing など）
"""

import os
from sqlalchemy import create_engine, text

from shared.app.celery_app import celery_app

DB_URL = os.getenv("DATABASE_URL")
_engine = create_engine(DB_URL, future=True)


@celery_app.task(name="shared.app.tasks.refresh_spot_congestion_mv")
def refresh_spot_congestion_mv():
    """
    ルーティンで spot_congestion_mv を更新するタスク。
    初回は CONCURRENTLY が使えない可能性があるため通常 REFRESH にフォールバック。
    """
    with _engine.begin() as conn:
        try:
            conn.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY spot_congestion_mv;"))
        except Exception:
            conn.execute(text("REFRESH MATERIALIZED VIEW spot_congestion_mv;"))
