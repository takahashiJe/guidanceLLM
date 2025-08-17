# -*- coding: utf-8 -*-
"""
対話受付 API
- /api/v1/chat/message : テキスト or 音声メッセージを受け付け（受理のみで 202 を返す）

要件:
- JSON と multipart/form-data の両方を受理
  - JSON: { session_id, message_text, lang, input_mode }
  - multipart: fields(session_id, lang, input_mode, [message_text]), files(audio_file)
- 少なくとも message_text か audio_file のどちらかが必要
- session_id は自分のセッションであることを検証（Session.session_id + user_id）
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Body, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from shared.app.database import get_db
from shared.app import models
from api_gateway.app.security import get_current_user

router = APIRouter(tags=["chat"])


# -----------------------------
# モデル
# -----------------------------
class ChatJSONRequest(BaseModel):
    session_id: str
    message_text: Optional[str] = None
    lang: str = "ja"
    input_mode: str = "text"


class ChatSubmitResponse(BaseModel):
    accepted: bool = True
    session_id: str


# -----------------------------
# /chat/message
# -----------------------------
@router.post("/chat/message", response_model=ChatSubmitResponse, status_code=202)
async def submit_message(
    request: Request,
    # JSON の場合
    json_body: Optional[ChatJSONRequest] = Body(None),
    # multipart の場合（フォーム + ファイル）
    session_id_form: Optional[str] = Form(None, alias="session_id"),
    message_text_form: Optional[str] = Form(None, alias="message_text"),
    lang_form: Optional[str] = Form(None, alias="lang"),
    input_mode_form: Optional[str] = Form(None, alias="input_mode"),
    audio_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    メインの対話受付。
    - text があればそのまま受け付け
    - audio があれば受け付け（STT などは Worker 側の責務）
    - 受け付けのみで 202 を返す（NFR-2-1: 応答速度）
    """
    # 入力の統合（JSON 優先、無ければ multipart）
    if json_body is not None:
        session_id = json_body.session_id
        message_text = json_body.message_text
        lang = json_body.lang
        input_mode = json_body.input_mode
        has_audio = False
    else:
        session_id = session_id_form
        message_text = message_text_form
        lang = lang_form or "ja"
        input_mode = input_mode_form or ("voice" if audio_file is not None else "text")
        has_audio = audio_file is not None

    # 最低限のバリデーション
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="session_id is required.",
        )

    if not (message_text or has_audio):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="message_text or audio_file is required.",
        )

    # セッションの所有者検証（session_id は文字列のカラム）
    sess = (
        db.query(models.Session)
        .filter(models.Session.session_id == session_id, models.Session.user_id == user.id)
        .first()
    )
    if not sess:
        raise HTTPException(status_code=404, detail="session not found")

    # ここでキュー投入や履歴保存を行う場合はこの下に実装
    # 本テストでは受理（202）で十分

    return ChatSubmitResponse(accepted=True, session_id=session_id)
