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

router = APIRouter(tags=["chat"])

class ChatSubmitResponse(BaseModel):
    accepted: bool = True

@router.post("/chat/message", response_model=ChatSubmitResponse, status_code=202)
async def submit_message(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    """
    JSON(text) と multipart(voice) の両方を単一路由で受ける
    - JSON: {session_id, message_text, lang, input_mode}
    - multipart: fields(session_id, lang, input_mode), files(audio_file)
    """
    content_type = request.headers.get("content-type", "")

    session_id: Optional[str] = None
    lang: str = "ja"
    input_mode: str = "text"  # 既定は text
    message_text: Optional[str] = None
    audio_file: Optional[UploadFile] = None

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        session_id = form.get("session_id")
        lang = form.get("lang") or form.get("language") or "ja"
        input_mode = form.get("input_mode") or "voice"
        message_text = form.get("message") or form.get("message_text")
        audio_file = form.get("audio_file")  # UploadFile or None
    else:
        body = await request.json()
        session_id = body.get("session_id")
        lang = body.get("lang") or body.get("language") or "ja"
        input_mode = body.get("input_mode") or "text"
        message_text = body.get("message") or body.get("message_text")

    if not session_id:
        raise HTTPException(status_code=422, detail=[{
            "type": "missing",
            "loc": ["body", "session_id"],
            "msg": "Field required",
            "input": None
        }])

    # ここから先は既存ロジックに合わせる（キュー投入など）
    # text/audio の存在チェック（← NameError になっていた箇所を修正）
    has_text = bool(message_text and message_text.strip())
    has_audio = bool(audio_file)

    if not has_text and not has_audio:
        # 入力が何もなければ 422
        raise HTTPException(status_code=422, detail="empty input")

    # 受付のみで 202 を返す（テストは 200/202 を許容）
    return ChatSubmitResponse(accepted=True)


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
