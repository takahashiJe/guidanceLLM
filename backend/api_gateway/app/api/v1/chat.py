# -*- coding: utf-8 -*-
# backend/api_gateway/app/api/v1/chat.py
"""
チャット受付とタスク結果ポーリングのAPI。
- 受理専用: POST /api/v1/chat/message -> 202 + {task_id, session_id, accepted}
- 結果取得: GET  /api/v1/chat/result/{task_id} -> Celery Result Backend を参照して状態/結果を返却
- JSON / multipart（音声）双方に対応
- 認証は任意（トークンがあれば user_id を付与）。トークンがなくても利用可。
"""

from __future__ import annotations

import base64
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from starlette.responses import JSONResponse

from api_gateway.app.security import get_current_user_optional
from shared.app.celery_app import celery_app
from celery.result import AsyncResult

# タスク名は shared.app.tasks の定数に揃える
from shared.app.tasks import TASK_ORCHESTRATE_CONVERSATION

router = APIRouter()


def _extract_optional_user_id(current_user) -> Optional[int]:
    """
    認証依存の user_id 抜き出し。
    - ORM(User) / dict の両対応
    - None なら匿名扱い
    """
    if not current_user:
        return None
    # ORM の可能性
    if hasattr(current_user, "id"):
        return int(current_user.id)
    # dict の可能性
    if isinstance(current_user, dict) and "user_id" in current_user:
        return int(current_user["user_id"])
    return None


@router.post("/message")
async def post_message(
    request: Request,
    # --- multipart/form-data フィールド（音声 or フォームテキスト）---
    session_id_form: Optional[str] = Form(default=None),
    lang_form: Optional[str] = Form(default=None),
    input_mode_form: Optional[str] = Form(default=None),
    audio_file: Optional[UploadFile] = File(default=None),
    # --- 共通：認証（任意）---
    current_user: Optional[object] = Depends(get_current_user_optional),
):
    """
    ユーザーメッセージ受付（非同期実行のトリガー）。
      - JSON / multipart の両方をサポート
      - 受理後は 202 + {task_id, session_id, accepted} を返し、
        結果は /api/v1/chat/result/{task_id} をポーリングで取得
    """
    user_id = _extract_optional_user_id(current_user)

    content_type = request.headers.get("content-type", "")
    is_json = "application/json" in content_type

    session_id: Optional[str] = None
    lang: str = "ja"
    input_mode: str = "text"
    message_text: Optional[str] = None
    audio_b64: Optional[str] = None

    if is_json:
        body = await request.json()
        session_id = body.get("session_id")
        lang = body.get("lang") or "ja"
        input_mode = body.get("input_mode") or "text"
        message_text = body.get("message_text")
        # JSON でも audio_b64 を受け付ける（任意）
        audio_b64 = body.get("audio_b64")
    else:
        # multipart/form-data
        session_id = session_id_form
        lang = lang_form or "ja"
        input_mode = input_mode_form or "voice"
        # 音声ファイルが来ていれば base64 化
        if audio_file is not None:
            data = await audio_file.read()
            if data:
                audio_b64 = base64.b64encode(data).decode("utf-8")

    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    # Celery へ投入する payload（Worker 側の orchestrate_conversation_task が受け取る形）
    payload = {
        "session_id": session_id,
        "user_id": user_id,       # 認証なしなら None
        "lang": lang,
        "input_mode": input_mode, # "text" | "voice"
    }
    # 片方のみ渡す
    if audio_b64:
        payload["audio_b64"] = audio_b64
    else:
        payload["message_text"] = (message_text or "").strip()

    # Celery へタスク投入（定数名に一致）
    # ※ Worker 起動コマンドは要件通り:
    #   celery -A shared.app.celery_app.celery_app worker --loglevel=info --pool=threads --concurrency=1
    async_result = celery_app.send_task(TASK_ORCHESTRATE_CONVERSATION, args=[payload])

    return JSONResponse(
        status_code=202,
        content={
            "accepted": True,
            "session_id": session_id,
            "task_id": async_result.id,
        },
    )


@router.get("/result/{task_id}")
async def get_result(task_id: str):
    """
    Celery の実行結果を返すポーリング用エンドポイント。
    - 200 OK + {task_id, status, result?, error?}
    - 未完了: PENDING / STARTED / RETRY 等の status を返す
    - 完了: SUCCESS → result, FAILURE → error を返す
    """
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id is required")

    ar = AsyncResult(task_id, app=celery_app)
    state = ar.state  # PENDING/STARTED/SUCCESS/FAILURE/RETRY

    # 失敗
    if state == "FAILURE":
        # 例外メッセージを文字列化して返す（テスト側が拾って FAIL にする）
        err = str(getattr(ar, "result", "")) or "unknown error"
        return {
            "task_id": task_id,
            "status": "FAILURE",
            "result": None,
            "error": err,
        }

    # 成功
    if state == "SUCCESS":
        result = ar.result  # Worker 側の戻り dict など
        return {
            "task_id": task_id,
            "status": "SUCCESS",
            "result": result,
        }

    # それ以外（未完了）
    return {
        "task_id": task_id,
        "status": state,
        "result": None,
    }
