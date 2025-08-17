# backend/shared/app/celery_app.py
# ------------------------------------------------------------
# Celery アプリ定義（Gateway/Worker 共通）
#  - 重要: include に 'worker.app.tasks' と 'shared.app.tasks' を明示指定
#    -> Worker 起動時に全タスクが確実に登録され、NotRegistered を防ぐ
#  - シリアライザ/タイムゾーン/ACK 運用などは現状要件に合わせて保守的に設定
# ------------------------------------------------------------
from __future__ import annotations

import os
from celery import Celery

BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1")

# include にタスク定義モジュールを明示。
# - shared.app.tasks: 共有のタスク（MV リフレッシュ、routing など）
# - worker.app.tasks: オーケストレーション/ナビ/STT/TTS など Worker 側の実行タスク
celery_app = Celery(
    "guidance",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=[
        "shared.app.tasks",
        "worker.app.tasks",
    ],
)

# ベーシックな運用設定（GPUリソース競合を避けるための late ack / prefetch=1）
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=os.getenv("TZ", "Asia/Tokyo"),
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    # 必要に応じてキューを分ける場合は routes を追加:
    # task_routes = {"orchestrate.*": {"queue": "orchestrate"}, ...}
)
