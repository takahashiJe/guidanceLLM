# backend/shared/celery_app.py
import os
from celery import Celery

# 環境変数から接続情報を取得
broker_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend_url = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1")

# Celeryアプリケーションのインスタンスを作成
celery_app = Celery(
    "worker",
    broker=broker_url,
    backend=result_backend_url,
    include=["worker.app.tasks"] # 実行するタスクが定義されているモジュールを指定
)

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
