# -*- coding: utf-8 -*-
"""
API Gateway: Chat / Navigation 受付
- /api/v1/chat/message : テキスト or 音声(multipart) を受け付け、Celery にディスパッチ
- /api/v1/chat/result/{task_id} : 非同期結果のポーリング取得
- /api/v1/navigation/start : ナビ開始を非同期トリガ（ガイド事前生成など）
"""

from __future__ import annotations

import base64
from typing import Optional, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status, Request
from fastapi.responses import JSONResponse

from api_gateway.app.security import get_current_user_optional
from shared.app.celery_app import celery_app
from shared.app.tasks import (
    TASK_ORCHESTRATE_CONVERSATION,
    TASK_START_NAVIGATION,
)
from celery.result import AsyncResult

# Chat用ルーター（既存パスと整合）
router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
# Navigation用ルーター（本ファイル内で定義して main から include する）
nav_router = APIRouter(prefix="/api/v1/navigation", tags=["navigation"])


def _extract_user_id(current_user: Any) -> Optional[int]:
    """User モデル or dict 双方に対応して user_id を取り出す。"""
    if current_user is None:
        return None
    # SQLAlchemy の User モデル想定
    if hasattr(current_user, "id"):
        return getattr(current_user, "id")
    # dict 想定（古い呼び出し互換）
    if isinstance(current_user, dict):
        return current_user.get("user_id")
    return None


def _enqueue_orchestrate_task(
    *,
    session_id: str,
    user_id: Optional[int],
    lang: str,
    input_mode: str,
    message_text: Optional[str],
    audio_b64: Optional[str],
) -> str:
    """Celery に orchestrate タスクを投入し、task_id を返す。"""
    payload = {
        "session_id": session_id,
        "user_id": user_id,
        "lang": lang or "ja",
        "input_mode": input_mode or "text",
    }
    if message_text:
        payload["message_text"] = message_text
    if audio_b64:
        payload["audio_b64"] = audio_b64

    async_result = celery_app.send_task(TASK_ORCHESTRATE_CONVERSATION, args=[payload])
    return async_result.id


@router.post("/message")
async def post_message(
    request: Request,
    # --- multipart/form-data フィールド（音声 or フォームテキスト）---
    session_id_form: Optional[str] = Form(default=None),
    lang_form: Optional[str] = Form(default=None),
    input_mode_form: Optional[str] = Form(default=None),
    audio_file: Optional[UploadFile] = File(default=None),
    # --- 共通：認証（任意）---
    current_user=Depends(get_current_user_optional),
):
    """
    ユーザーメッセージ受付。
    - JSON / multipart の両方をサポート
    - 受理後は 202 + {task_id} を返し、結果は /api/v1/chat/result/{task_id} をポーリング
    """
    user_id = _extract_user_id(current_user)
    content_type = request.headers.get("content-type", "")
    is_multipart = content_type.startswith("multipart/form-data")

    if is_multipart:
        # multipart/form-data: 音声 or テキスト（Form）想定
        if not session_id_form:
            raise HTTPException(status_code=400, detail="session_id は必須です。")

        lang = (lang_form or "ja").strip()
        input_mode = (input_mode_form or "voice").strip()
        message_text = None
        audio_b64 = None

        if audio_file is not None:
            data = await audio_file.read()
            if not data:
                raise HTTPException(status_code=400, detail="音声ファイルが空です。")
            audio_b64 = base64.b64encode(data).decode("utf-8")

        # テキスト（Form に message_text が来る場合）
        if not audio_b64:
            form = await request.form()
            mt = (form.get("message_text") or "").strip()
            message_text = mt or None

        task_id = _enqueue_orchestrate_task(
            session_id=session_id_form,
            user_id=user_id,
            lang=lang,
            input_mode=input_mode,
            message_text=message_text,
            audio_b64=audio_b64,
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"accepted": True, "task_id": task_id, "session_id": session_id_form},
        )

    # JSON: {session_id, lang, input_mode, message_text?, audio_b64?}
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON ボディのパースに失敗しました。")

    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id は必須です。")

    lang = (body.get("lang") or "ja").strip()
    input_mode = (body.get("input_mode") or "text").strip()
    message_text = (body.get("message_text") or "").strip() or None
    audio_b64 = (body.get("audio_b64") or "").strip() or None

    task_id = _enqueue_orchestrate_task(
        session_id=session_id,
        user_id=user_id,
        lang=lang,
        input_mode=input_mode,
        message_text=message_text,
        audio_b64=audio_b64,
    )
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"accepted": True, "task_id": task_id, "session_id": session_id},
    )


@router.get("/result/{task_id}")
async def get_message_result(task_id: str):
    """
    /message の非同期結果をポーリングで返す。
    - READY/SUCCESS 相当なら result も返す
    """
    if not task_id or task_id == "None":
        raise HTTPException(status_code=404, detail="task_id が不正です。")

    ar: AsyncResult = celery_app.AsyncResult(task_id)
    state = ar.state  # PENDING, STARTED, SUCCESS, FAILURE, RETRY, REVOKED

    body = {"task_id": task_id, "status": state}

    if state == "SUCCESS":
        try:
            result = ar.get(propagate=False)
        except Exception:
            result = None
        body["result"] = result

    if state == "FAILURE":
        body["error"] = str(ar.info)

    return JSONResponse(status_code=200, content=body)


# =========================
# Navigation: start
# =========================
@nav_router.post("/start")
async def navigation_start(
    payload: dict,
    current_user=Depends(get_current_user_optional),
):
    """
    ナビゲーション開始を非同期でトリガー。
    - 入力: { "session_id": str, "lang": "ja"|"en"|"zh" }
    - 返却: 202 + {accepted, task_id}
    """
    session_id = (payload.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id は必須です。")

    lang = (payload.get("lang") or "ja").strip()
    user_id = _extract_user_id(current_user)

    task_payload = {"session_id": session_id, "user_id": user_id, "lang": lang}
    async_result = celery_app.send_task(TASK_START_NAVIGATION, args=[task_payload])
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"accepted": True, "task_id": async_result.id, "session_id": session_id},
    )
