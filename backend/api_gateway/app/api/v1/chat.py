# backend/api_gateway/app/api/v1/chat.py
# ユーザーのメッセージ（テキスト or 音声）を受け取り、Celery タスクに非同期投入。
# - audio は multipart/form-data で受信→base64 化してタスクへ
# - 202 Accepted を返し、フロントはポーリングで結果取得（設計どおり）

import base64
import os
from typing import Optional, Literal

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from api_gateway.app.security import get_current_user, get_current_user_optional
from shared.app.database import get_db
from shared.app.celery_app import celery_app
from shared.app.schemas import ChatSubmitResponse, NavigationStartRequest, NavigationLocationRequest
from shared.app.tasks import (
    orchestrate_message,  # 既存の総合タスク（テキスト/音声どちらも渡せる前提）
)

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
    """
    メインの対話受付。
    - text があればそのまま受け付け
    - audio があれば base64 に変換して Celery に委譲（STT は Worker 側）
    - 受け付けのみで 202 を返す（NFR-2-1: 応答速度）
    """
    if not text and not audio:
        raise HTTPException(status_code=400, detail="text か audio のいずれかが必要です。")

    audio_b64 = None
    media_type = None
    if audio:
        content = await audio.read()
        audio_b64 = base64.b64encode(content).decode("utf-8")
        media_type = audio.content_type or "audio/wav"

    payload = {
        "session_id": session_id,
        "lang": lang,
        "user_id": getattr(current_user, "id", None),  # 未ログインでも None 許容
        "text": text,
        "audio_b64": audio_b64,
        "media_type": media_type,
    }

    # オーケストレーションへ非同期委譲（意図分類→必要なら STT→以降の分岐）
    orchestrate_message(payload)
    return {"accepted": True, "session_id": session_id}


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
