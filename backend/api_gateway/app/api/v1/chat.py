# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Optional
from starlette.datastructures import UploadFile as StarletteUploadFile
from fastapi import UploadFile as FastapiUploadFile

from fastapi import APIRouter, Depends, HTTPException, status, Body, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from shared.app.database import get_db
from shared.app import models
from api_gateway.app.security import get_current_user

router = APIRouter(tags=["chat"])

class ChatJSONRequest(BaseModel):
    session_id: str
    message_text: Optional[str] = None
    lang: str = "ja"
    input_mode: str = "text"

class ChatSubmitResponse(BaseModel):
    accepted: bool = True
    session_id: str

@router.post("/chat/message", response_model=ChatSubmitResponse, status_code=202)
async def submit_message(
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    JSON と multipart/form-data 両対応（手動判別）
    - JSON: { session_id, message_text, lang, input_mode }
    - multipart: fields(session_id, lang, input_mode, [message_text]), files(audio_file)
    """
    content_type = (request.headers.get("content-type") or "").lower()
    session_id: Optional[str] = None
    message_text: Optional[str] = None
    lang = "ja"
    input_mode = "text"
    has_audio = False

    if "application/json" in content_type:
        # JSON を手動で読む（Body(...) は使わない）
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON.")
        try:
            parsed = ChatJSONRequest.model_validate(data)
        except ValidationError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=e.errors())
        session_id = parsed.session_id
        message_text = parsed.message_text
        lang = parsed.lang
        input_mode = parsed.input_mode

    elif "multipart/form-data" in content_type:
        form = await request.form()
        session_id = form.get("session_id") or None
        message_text = form.get("message_text") or None
        lang = form.get("lang") or "ja"
        input_mode = form.get("input_mode") or ("voice" if "audio_file" in form else "text")

        audio_field = form.get("audio_file")

        # --- 頑健な has_audio 判定 ---
        has_audio = False
        if isinstance(audio_field, (FastapiUploadFile, StarletteUploadFile)):
            has_audio = True
        elif hasattr(audio_field, "file"):
            # Starlette UploadFile 互換オブジェクトなら .file を持つことが多い
            has_audio = audio_field.file is not None
        elif hasattr(audio_field, "filename"):
            # まれにモックで filename だけある場合
            has_audio = bool(audio_field.filename)

    else:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported Content-Type",
        )

    if not session_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="session_id is required.")
    if not (message_text or has_audio):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="message_text or audio_file is required.")

    # セッションの所有者検証（session_id は文字列カラム）
    sess = (
        db.query(models.Session)
        .filter(models.Session.session_id == session_id, models.Session.user_id == user.id)
        .first()
    )
    if not sess:
        raise HTTPException(status_code=404, detail="session not found")

    # 受理のみ
    return ChatSubmitResponse(accepted=True, session_id=session_id)
