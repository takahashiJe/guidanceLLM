# shared/app/celery_app.py
import os
from celery import Celery

BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1")

celery_app = Celery("chokai_shared", broker=BROKER_URL, backend=RESULT_BACKEND)

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

celery_app.conf.timezone = 'Asia/Tokyo'
