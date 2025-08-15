# -*- coding: utf-8 -*-
"""
共有Celeryタスク定義。
- ここにマテビュー更新タスクを追加
- 既存のタスク名・インポートは壊さない（orchestrate, STT/TTS, routing など）
"""

import os
from datetime import date
from celery import shared_task

from shared.app.celery_app import celery_app
from shared.app.database import SessionLocal
from sqlalchemy import create_engine, text

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

@celery_app.task(name="worker.app.tasks.refresh_congestion_mv_task")
def refresh_congestion_mv_task() -> str:
    """
    マテビュー 'congestion_by_date_spot' を CONCURRENTLY でリフレッシュ。
    - 事前にユニークインデックスが必要（init_db_script で作成）
    """
    mv = "congestion_by_date_spot"
    with SessionLocal() as db:
        try:
            db.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv};"))
            db.commit()
            return "ok"
        except Exception as e:
            db.rollback()
            # MV未作成などの場合はログのみ（初回ブート順の差異考慮）
            return f"failed: {e}"