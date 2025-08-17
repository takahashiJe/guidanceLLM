# -*- coding: utf-8 -*-
"""
Chat / Navigation エンドポイント
- JSON と multipart の両対応で /api/v1/chat/message を公開
- Celery へ投げて task_id を返す（/api/v1/chat/result/{task_id} でポーリング）
- /api/v1/navigation/start も公開
"""

from __future__ import annotations

import base64
import json
from typing import Optional

from fastapi import APIRouter, Depends, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse

from api_gateway.app.security import get_current_user_optional  # Optional[models.User]
from shared.app.celery_app import celery_app
from shared.app import models

router = APIRouter(prefix="/chat")


# Celery タスク名（ワーカー側に実在する関数名に合わせる）
#   - worker/app/tasks.py の @celery_app.task で name 明示が無い場合は
#     デフォルトで "worker.app.tasks.<func_name>" になります。
TASK_ORCHESTRATE = "worker.app.tasks.orchestrate_conversation_task"
TASK_NAV_START = "worker.app.tasks.navigation_start_task"


def _parse_body_any(request: Request) -> dict:
    """
    JSON でも multipart/form-data でも、同一の辞書に正規化する。
    - multipart: session_id, lang, input_mode はフォーム値
                 audio_file があれば base64 化して audio_b64 に格納
                 （STT はワーカー側）
    - JSON: そのままロード
    """
    if request.headers.get("content-type", "").startswith("application/json"):
        # JSON
        return {}

    # multipart の場合はここでフォームを読む（エンドポイント関数側で受け取るため未使用）
    return {}


@router.post("/chat/message")
async def post_message(
    request: Request,
    # --- multipart/form-data の場合に受け取るフォーム項目 ---
    session_id_form: Optional[str] = Form(default=None),
    lang_form: Optional[str] = Form(default=None),
    input_mode_form: Optional[str] = Form(default=None),
    audio_file: Optional[UploadFile] = File(default=None),
    # --- 認証：任意（無くても受付）---
    current_user: Optional[models.User] = Depends(get_current_user_optional),
):
    """
    ユーザーメッセージ受付。
    - JSON / multipart の両方をサポート
    - 受理後は 202 + {task_id} を返し、結果は /api/v1/chat/result/{task_id} をポーリング
    """
    user_id = current_user.id if isinstance(current_user, models.User) else None

    # Content-Type を見て分岐
    if request.headers.get("content-type", "").startswith("application/json"):
        body = await request.json()
        session_id = body.get("session_id")
        lang = body.get("lang", "ja")
        input_mode = body.get("input_mode", "text")
        message_text = body.get("message_text")
        audio_b64 = body.get("audio_b64")  # JSON で渡された場合も許容
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id は必須です。")
    else:
        # multipart/form-data
        form = await request.form()
        session_id = session_id_form or form.get("session_id")
        lang = (lang_form or form.get("lang") or "ja")
        input_mode = (input_mode_form or form.get("input_mode") or "voice")
        message_text = form.get("message_text")
        audio_b64 = None
        if audio_file:
            # 音声は base64 へ（実際の STT はワーカー側）
            content = await audio_file.read()
            audio_b64 = base64.b64encode(content).decode("utf-8")
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id は必須です。")

    # Celery へ投入
    payload = {
        "session_id": session_id,
        "user_id": user_id,
        "lang": lang,
        "input_mode": input_mode,
        "message_text": message_text,
        "audio_b64": audio_b64,
    }
    try:
        async_result = celery_app.send_task(TASK_ORCHESTRATE, args=[payload])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"タスク投入に失敗しました: {e}")

    # 202 Accepted + task_id を返す（テスト要件）
    return JSONResponse(
        status_code=202,
        content={
            "accepted": True,
            "session_id": session_id,
            "task_id": async_result.id,
        },
    )


@router.get("/chat/result/{task_id}")
async def get_chat_result(task_id: str):
    """
    Celery の AsyncResult をポーリングするためのエンドポイント。
    """
    if not task_id or task_id.lower() == "none":
        raise HTTPException(status_code=404, detail="task_id が不正です。")
    try:
        res = celery_app.AsyncResult(task_id)
        state = res.state  # PENDING / STARTED / RETRY / FAILURE / SUCCESS
        body = {"task_id": task_id, "status": state}
        if res.ready():
            try:
                body["result"] = res.get(propagate=False)
            except Exception as e:
                body["status"] = "FAILURE"
                body["error"] = str(e)
        return JSONResponse(status_code=200, content=body)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"結果参照に失敗しました: {e}")


@router.post("/navigation/start")
async def navigation_start(
    payload: dict,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
):
    """
    ナビゲーション開始（ガイド事前生成などのトリガー）
    - E2E テストは 200/202 のみ確認
    - 実処理はワーカー側の navigation_start_task に委譲
    """
    session_id = payload.get("session_id")
    lang = payload.get("lang", "ja")
    user_id = current_user.id if isinstance(current_user, models.User) else None
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id は必須です。")

    args = [{"session_id": session_id, "user_id": user_id, "lang": lang}]
    try:
        async_result = celery_app.send_task(TASK_NAV_START, args=args)
    except Exception as e:
        # 起動順の都合などでワーカーが未登録でも 202 を返すのは避け、
        # ここでは明示的に 500 を返す（要件に合わせて調整可）
        raise HTTPException(status_code=500, detail=f"ナビ開始のタスク投入に失敗: {e}")

    return JSONResponse(
        status_code=202,
        content={"accepted": True, "session_id": session_id, "task_id": async_result.id},
    )
