# backend/shared/app/tasks.py
"""
API Gateway から Celery Worker（worker/app/tasks.py 実装）へ
send_task で非同期依頼するための “薄い窓口”。
タスク名の一貫性と引数形の型安全（軽め）を担保します。
"""
from typing import Dict, Any
from shared.app.celery_app import celery_app

# Worker 側で定義されているタスク名（must be in worker/app/tasks.py）
TASK_ORCHESTRATE_CONVERSATION = "worker.tasks.orchestrate_conversation_task"
TASK_START_NAVIGATION = "worker.tasks.start_navigation_task"
TASK_UPDATE_LOCATION = "worker.tasks.update_location_task"

def dispatch_orchestrate_conversation(payload: Dict[str, Any]) -> str:
    """
    payload 例:
    {
      "session_id": "...",
      "user_id": 123,
      "language": "ja",
      "dialogue_mode": "text" | "voice",
      "text": "...",                     # 任意
      "audio_filename": "xxx.wav",       # 任意
      "audio_bytes_b64": "...."          # 任意
    }
    """
    async_result = celery_app.send_task(TASK_ORCHESTRATE_CONVERSATION, args=[payload])
    return async_result.id

def dispatch_start_navigation(payload: Dict[str, Any]) -> str:
    """
    payload: { "session_id": "...", "user_id": 123 }
    """
    async_result = celery_app.send_task(TASK_START_NAVIGATION, args=[payload])
    return async_result.id

def dispatch_update_location(payload: Dict[str, Any]) -> str:
    """
    payload: { "session_id": "...", "lat": 0.0, "lon": 0.0, ... }
    """
    async_result = celery_app.send_task(TASK_UPDATE_LOCATION, args=[payload])
    return async_result.id
