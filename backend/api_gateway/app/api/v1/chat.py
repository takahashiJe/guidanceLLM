# backend/api_gateway/app/api/v1/chat.py
# ユーザーのメッセージ（テキスト or 音声）を受け取り、Celery タスクに非同期投入。
# - audio は multipart/form-data で受信→base64 化してタスクへ
# - 202 Accepted を返し、フロントはポーリングで結果取得（設計どおり）

import base64
import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from api_gateway.app.security import get_current_user
from shared.app.database import get_db
from shared.app.celery_app import celery_app
from shared.app.schemas import ChatSubmitResponse, NavigationStartRequest, NavigationLocationRequest

load_dotenv()

# タスク名は環境変数で上書き可能（Worker 側の実装に合わせられる）
ORCH_TASK_NAME = os.getenv("ORCH_TASK_NAME", "orchestrate_conversation")
NAV_START_TASK_NAME = os.getenv("NAV_START_TASK_NAME", "navigation.start")
NAV_LOCATION_TASK_NAME = os.getenv("NAV_LOCATION_TASK_NAME", "navigation.location")

router = APIRouter()


@router.post("/chat/message", response_model=ChatSubmitResponse, status_code=202)
async def submit_message(
    session_id: str = Form(..., description="フロント生成のsession_id"),
    message: Optional[str] = Form(None, description="テキストメッセージ（音声がある場合は省略可）"),
    language: Optional[str] = Form("ja", description="ユーザーの選択言語（ja/en/zh）"),
    audio_file: Optional[UploadFile] = File(None, description="音声ファイル（任意）"),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    # テキスト or 音声のいずれかは必須
    if not message and not audio_file:
        raise HTTPException(status_code=400, detail="message or audio_file is required")

    audio_b64 = None
    audio_mime = None
    audio_name = None
    if audio_file:
        raw = await audio_file.read()
        audio_b64 = base64.b64encode(raw).decode("utf-8")
        audio_mime = audio_file.content_type or "application/octet-stream"
        audio_name = audio_file.filename or "audio_input"

    # Celery タスクに投入
    # Worker 側のタスクは session_id / user_id / message / language / audio_* を受け取る想定
    task = celery_app.send_task(
        ORCH_TASK_NAME,
        kwargs={
            "session_id": session_id,
            "user_id": str(user.id),
            "message": message,
            "language": language,
            "audio_b64": audio_b64,
            "audio_mime": audio_mime,
            "audio_name": audio_name,
        },
    )

    return ChatSubmitResponse(task_id=task.id, accepted=True)


@router.post("/navigation/start", status_code=202)
def navigation_start(
    payload: NavigationStartRequest,
    user=Depends(get_current_user),
):
    task = celery_app.send_task(
        NAV_START_TASK_NAME,
        kwargs={
            "session_id": payload.session_id,
            "user_id": str(user.id),
            "language": payload.language or "ja",
        },
    )
    return {"task_id": task.id, "accepted": True}


@router.post("/navigation/location", status_code=202)
def navigation_location(
    payload: NavigationLocationRequest,
    user=Depends(get_current_user),
):
    task = celery_app.send_task(
        NAV_LOCATION_TASK_NAME,
        kwargs={
            "session_id": payload.session_id,
            "user_id": str(user.id),
            "lat": payload.lat,
            "lng": payload.lng,
        },
    )
    return {"task_id": task.id, "accepted": True}
