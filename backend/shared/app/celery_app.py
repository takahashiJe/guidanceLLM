# shared/app/celery_app.py
from __future__ import annotations
import os
from celery import Celery
from celery.schedules import crontab

BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1")
REFRESH_EVERY_MIN = int(os.getenv("MV_REFRESH_MINUTES", "5"))

celery_app = Celery("guidanceLLM", broker=BROKER_URL, backend=RESULT_BACKEND, include=[
    "shared.app.tasks",
])

celery_app.conf.timezone = os.getenv("TZ", "Asia/Tokyo")

# Celeryの設定（オプション）
celery_app.conf.update(
    task_track_started=True,
    # ワーカーが一度に受け取るタスク数を1に制限する
    # これにより、長時間タスクが他のタスクの実行をブロックするのを防ぐ
    worker_prefetch_multiplier=1,

    # タスクが成功または失敗した後にブローカーに通知（ack）を送る
    # これにより、ワーカーが処理中にクラッシュしてもタスクが失われない
    task_acks_late=True,
)

cron = os.getenv("CONGESTION_MV_REFRESH_CRON", "").strip()
if cron:
    minute, hour, day_of_month, month, day_of_week = cron.split()
    schedule = crontab(minute=minute, hour=hour, day_of_month=day_of_month, month_of_year=month, day_of_week=day_of_week)
else:
    # 既定は5分おき
    schedule = crontab(minute="*/5")

celery_app.conf.beat_schedule = {
    "refresh-congestion-mv-interval": {
        "task": "worker.app.tasks.refresh_congestion_mv_task",
        "schedule": crontab(minute=f"*/{REFRESH_EVERY_MIN}"),
        "options": {"queue": "default"},
    }
}