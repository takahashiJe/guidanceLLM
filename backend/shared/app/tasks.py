# backend/shared/app/tasks.py

from shared.app.celery_app import celery_app

# このファイルは、Celeryタスクの「シグネチャ（名前と引数）」を定義する場所です。
# API Gatewayはここをインポートしてタスクを呼び出し、
# Workerはここをインポートしてタスクの実装を登録します。

# 対話オーケストレーションタスク
orchestrate_conversation_task = celery_app.task(name="orchestrate_conversation_task")

# ナビゲーション開始タスク
start_navigation_task = celery_app.task(name="start_navigation_task")

# ナビゲーション中の位置情報更新タスク
update_location_task = celery_app.task(name="update_location_task")

# (将来的に) ナビゲーションイベント処理タスク
# handle_navigation_event_task = celery_app.task(name="handle_navigation_event_task")
