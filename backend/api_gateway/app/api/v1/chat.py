# backend/api_gateway/app/api/v1/chat.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from typing import Optional, Any, Iterable

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi import UploadFile as FastAPIUploadFile
from starlette.datastructures import UploadFile as StarletteUploadFile
from sqlalchemy.orm import Session

from shared.app.database import get_db
from shared.app import models
from api_gateway.app.security import get_current_user

router = APIRouter(tags=["chat"])

# 上限（バイト）。未設定なら 10MB
MAX_AUDIO_BYTES = int(os.getenv("CHAT_MAX_AUDIO_BYTES", str(10 * 1024 * 1024)))
ALLOWED_AUDIO_MIME = {
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "audio/mp3",
    "audio/webm",
}


def _is_upload_file(obj: Any) -> bool:
    """FastAPI/Starlette 双方の UploadFile、または .file を持つものを UploadFile と見なす。"""
    if isinstance(obj, (FastAPIUploadFile, StarletteUploadFile)):
        return True
    return hasattr(obj, "file") and obj.file is not None


def _pick_upload_file(value: Any) -> Optional[Any]:
    """単一/複数/その他の形で入ってくる 'audio_file' から UploadFile を頑健に抜き出す。"""
    if _is_upload_file(value):
        return value
    if isinstance(value, (list, tuple)):
        for v in value:
            if _is_upload_file(v):
                return v
    return None


def _validate_voice_file(file: Any) -> None:
    """音声ファイルのサイズ/MIME を簡易チェック。"""
    # サイズチェック
    try:
        file.file.seek(0, 2)
        size = file.file.tell()
        file.file.seek(0)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid audio file")

    if MAX_AUDIO_BYTES and size > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="audio file too large")

    # MIME（ざっくり）
    ctype = (getattr(file, "content_type", "") or "").lower()
    if ALLOWED_AUDIO_MIME and ctype and ctype not in ALLOWED_AUDIO_MIME:
        raise HTTPException(
            status_code=415, detail=f"unsupported audio content-type: {ctype}"
        )


@router.post("/chat/message", status_code=202)
async def submit_message(
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    JSON と multipart/form-data の両対応（手動判別）
    - JSON: { session_id, message_text, lang, input_mode }
    - multipart: fields(session_id, lang, input_mode, [message_text]), files(audio_file)
    """
    content_type = (request.headers.get("content-type") or "").lower()

    session_id: Optional[str] = None
    message_text: Optional[str] = None
    lang = "ja"
    input_mode = "text"
    audio_file: Optional[Any] = None

    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="invalid json body")
        if not isinstance(body, dict):
            raise HTTPException(status_code=422, detail="invalid json body")

        session_id = body.get("session_id") or None
        message_text = body.get("message_text") or None
        lang = body.get("lang") or "ja"
        input_mode = (body.get("input_mode") or "text").lower()

    elif "multipart/form-data" in content_type:
        form = await request.form()

        session_id = form.get("session_id") or None
        message_text = form.get("message_text") or None
        lang = form.get("lang") or "ja"
        input_mode = (form.get("input_mode") or ("voice" if "audio_file" in form else "text")).lower()

        # get で拾う
        maybe_file = form.get("audio_file")
        audio_file = _pick_upload_file(maybe_file)

        # getlist 側にある場合も考慮
        if audio_file is None and hasattr(form, "getlist"):
            try:
                lst: Iterable[Any] = form.getlist("audio_file")
            except Exception:
                lst = []
            audio_file = _pick_upload_file(lst)
    else:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Supported: application/json or multipart/form-data",
        )

    # 共通バリデーション
    if not session_id:
        raise HTTPException(status_code=422, detail="session_id is required.")

    # モードに応じた必須チェック
    if input_mode == "text":
        if not message_text:
            raise HTTPException(
                status_code=422, detail="message_text is required for text mode."
            )
    elif input_mode == "voice":
        if not _is_upload_file(audio_file):
            raise HTTPException(
                status_code=422, detail="audio_file is required for voice mode."
            )
        _validate_voice_file(audio_file)
    else:
        raise HTTPException(status_code=422, detail=f"unknown input_mode: {input_mode}")

    # セッションの所有者検証（session_id は文字列カラム）
    sess = (
        db.query(models.Session)
        .filter(
            models.Session.session_id == session_id,
            models.Session.user_id == user.id,
        )
        .first()
    )
    if not sess:
        raise HTTPException(status_code=404, detail="session not found")

    # 受理のみ（音声があればここでアップロード先にストリームやキュー投入等）
    return {"accepted": True, "session_id": session_id}