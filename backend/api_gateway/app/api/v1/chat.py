# -*- coding: utf-8 -*-
"""
/api/v1/chat エンドポイント群
- JSON / multipart 両対応の /message
- Celery task の状態ポーリング /result/{task_id}
- 既存仕様を壊さない：/api/v1 は main.py 側で付与。ここでは "/chat" から開始。
"""

from __future__ import annotations

import base64
import os
from typing import Optional, Dict, Any, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from shared.app.celery_app import celery_app
from api_gateway.app.security import get_current_user_optional
from shared.app import models

# Celery タスク名（Worker 側の @celery_app.task(name=...) と一致させる）
# 既存の shared.app.tasks への import ではなく、文字列で明示することで依存を減らす
TASK_ORCHESTRATE_CONVERSATION = os.getenv(
    "TASK_ORCHESTRATE_CONVERSATION", "orchestrate.conversation"
)

router = APIRouter(prefix="/chat", tags=["chat"])


def _extract_json_payload(body: Dict[str, Any]) -> Tuple[str, Optional[str], str, Optional[str]]:
    """
    JSON 受付時の必須/任意フィールド抽出。
    戻り値: (session_id, message_text, lang, audio_b64)
    """
    session_id = body.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    message_text = body.get("message_text")
    lang = body.get("lang", "ja")
    audio_b64 = body.get("audio_b64")  # 任意（音声のベース64文字列を直接送る場合）

    return session_id, message_text, lang, audio_b64


async def _extract_multipart_payload(
    session_id_form: Optional[str],
    lang_form: Optional[str],
    input_mode_form: Optional[str],
    audio_file: Optional[UploadFile],
) -> Tuple[str, Optional[str], str, Optional[str]]:
    """
    multipart/form-data 受付時の必須/任意フィールド抽出。
    - テキストはフォームに text として送る想定はなく、multipart の場合は通常音声
    - 音声は base64 化して Worker に渡す
    戻り値: (session_id, message_text, lang, audio_b64)
    """
    if not session_id_form:
        raise HTTPException(status_code=400, detail="session_id is required")

    lang = (lang_form or "ja").strip()

    # 音声があれば base64 化
    audio_b64: Optional[str] = None
    if audio_file:
        content = await audio_file.read()
        # 最小限のヘッダーでも受け付ける（E2E のダミーWAV考慮）
        audio_b64 = base64.b64encode(content).decode("utf-8")

    # multipart では message_text を運ばない前提（音声中心）
    message_text: Optional[str] = None
    return session_id_form, message_text, lang, audio_b64


@router.post("/message")
async def post_message(
    request: Request,
    # --- multipart/form-data フィールド（音声 or フォームテキスト）---
    session_id_form: Optional[str] = Form(default=None),
    lang_form: Optional[str] = Form(default=None),
    input_mode_form: Optional[str] = Form(default=None),
    audio_file: Optional[UploadFile] = File(default=None),
    # --- 共通：認証（任意）---
    current_user: Optional[models.User] = Depends(get_current_user_optional),
):
    """
    ユーザーメッセージ受付。
    - JSON / multipart の両方をサポート
    - 受理後は 202 + {task_id} を返し、結果は /api/v1/chat/result/{task_id} をポーリング
    """
    user_id: Optional[int] = current_user.id if isinstance(current_user, models.User) else None

    # JSON or multipart を自動判定
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="invalid JSON body")

        session_id, message_text, lang, audio_b64 = _extract_json_payload(body)
        input_mode = (body.get("input_mode") or ("voice" if audio_b64 else "text")).strip()

    else:
        # multipart/form-data として処理
        session_id, message_text, lang, audio_b64 = await _extract_multipart_payload(
            session_id_form, lang_form, input_mode_form, audio_file
        )
        input_mode = (input_mode_form or ("voice" if audio_b64 else "text")).strip()

    # Celery に渡す payload を組み立て
    payload: Dict[str, Any] = {
        "session_id": session_id,
        "user_id": user_id,
        "lang": lang,
        "input_mode": input_mode,
    }
    if message_text:
        payload["message_text"] = message_text
    if audio_b64:
        payload["audio_b64"] = audio_b64

    # Celery タスクを投入（非同期）
    try:
        async_result = celery_app.send_task(TASK_ORCHESTRATE_CONVERSATION, args=[payload])
    except Exception as e:
        # ブローカー未起動など
        raise HTTPException(status_code=503, detail=f"failed to enqueue task: {e}")

    # 受理応答（tests は task_id を期待）
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"accepted": True, "session_id": session_id, "task_id": async_result.id},
    )


class TaskResultResponse(BaseModel):
    task_id: str
    status: str
    result: Optional[Dict[str, Any]] = None
    # 追加: 失敗時の簡易情報
    error: Optional[str] = None


@router.get("/result/{task_id}", response_model=TaskResultResponse)
async def get_result(task_id: str):
    """
    Celery の AsyncResult を参照して状態/結果を返す。
    - SUCCESS: result を返す（Worker 側の orchestrate_conversation_task の戻り値）
    - PENDING/RETRY: 202 with status
    - FAILURE: 200 で status=FAILURE + 簡易エラー
    """
    from celery.result import AsyncResult

    if task_id in (None, "", "None"):
        # E2E の防御策
        raise HTTPException(status_code=404, detail="task_id not found")

    ar = AsyncResult(task_id, app=celery_app)
    state = ar.state or "PENDING"

    if state == "SUCCESS":
        try:
            res = ar.get(propagate=False)
        except Exception as e:
            return TaskResultResponse(task_id=task_id, status="FAILURE", error=str(e))
        return TaskResultResponse(task_id=task_id, status="SUCCESS", result=res)

    if state in ("PENDING", "RETRY", "STARTED", "RECEIVED"):
        # 進行中 → 202
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=TaskResultResponse(task_id=task_id, status=state).model_dump(),
        )

    if state == "FAILURE":
        # 失敗 → 200 で失敗情報
        try:
            info = str(ar.result) if ar.result else None
        except Exception:
            info = None
        return TaskResultResponse(task_id=task_id, status="FAILURE", error=info)

    # その他状態（REVOKEDなど）→ 一旦 200
    return TaskResultResponse(task_id=task_id, status=state)
