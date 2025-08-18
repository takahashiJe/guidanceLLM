# backend/api_gateway/app/api/v1/chat.py
# ============================================================
# 役割:
# - 対話メッセージ受付のフロントドア
# - JSON と multipart/form-data（音声）を単一のエンドポイントで受理
# - 音声は base64 化して payload に格納し、Celery のオーケストレータタスクに委譲
# - ポーリングは別 API（/sessions/restore）で取得する前提を維持
# ============================================================

from __future__ import annotations

import base64
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse

from api_gateway.app.security import get_current_user_optional
from shared.app.celery_app import celery_app
from shared.app.tasks import TASK_ORCHESTRATE_CONVERSATION

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


async def _read_multipart(request: Request) -> Dict[str, Any]:
    """
    multipart/form-data を読み取り、必要なフィールドを辞書化する。
    - fields:
        session_id: str (必須)
        lang: str (任意, 既定 ja)
        input_mode: str (任意, "voice" 推奨)
        message_text: str (任意, 音声がない場合はこちらを採用)
        audio: UploadFile (任意)
    """
    form = await request.form()
    session_id = (form.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id は必須です。")

    lang = (form.get("lang") or "ja").strip().lower()
    # 言語のバリデーションは緩やかに（ja/en/zh を推奨）
    if lang not in {"ja", "en", "zh"}:
        lang = "ja"

    input_mode = (form.get("input_mode") or "").strip().lower()
    message_text = (form.get("message_text") or "").strip()

    audio_file: Optional[UploadFile] = form.get("audio")  # type: ignore
    audio_b64 = None
    if audio_file and hasattr(audio_file, "read"):
        # 注意: 大きなファイルは API Gateway で一旦メモリに乗る。必要に応じて制限を導入。
        audio_bytes = await audio_file.read()
        if audio_bytes:
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
            # 音声があるなら input_mode は voice に寄せる
            if not input_mode:
                input_mode = "voice"

    if not message_text and not audio_b64:
        raise HTTPException(status_code=400, detail="message_text または audio のいずれかが必要です。")

    return {
        "session_id": session_id,
        "lang": lang,
        "input_mode": input_mode or ("voice" if audio_b64 else "text"),
        "message_text": message_text or None,
        "audio_b64": audio_b64,
    }


async def _read_json(request: Request) -> Dict[str, Any]:
    """
    application/json を読み取り、必要なフィールドを辞書化する。
    - body:
        {
          "session_id": str,   # 必須
          "lang": "ja"|"en"|"zh", # 任意, 既定 ja
          "input_mode": "text"|"voice", # 任意
          "message_text": str, # 任意
          "audio_b64": str     # 任意（voice のとき）
        }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON のパースに失敗しました。")

    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id は必須です。")

    lang = (body.get("lang") or "ja").strip().lower()
    if lang not in {"ja", "en", "zh"}:
        lang = "ja"

    input_mode = (body.get("input_mode") or "").strip().lower()
    message_text = (body.get("message_text") or None)
    audio_b64 = (body.get("audio_b64") or None)

    if not message_text and not audio_b64:
        raise HTTPException(status_code=400, detail="message_text または audio_b64 のいずれかが必要です。")

    # voice 指定がない場合でも、audio_b64 があれば voice とみなす
    if not input_mode:
        input_mode = "voice" if audio_b64 else "text"

    return {
        "session_id": session_id,
        "lang": lang,
        "input_mode": input_mode,
        "message_text": message_text,
        "audio_b64": audio_b64,
    }


@router.post("/message", summary="ユーザー入力（テキスト/音声）の受付", status_code=202)
async def post_message(
    request: Request,
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """
    単一エンドポイントで JSON と multipart を受け付ける。
    - application/json:
        {session_id, lang?, input_mode?, message_text?, audio_b64?}
    - multipart/form-data:
        fields: session_id (必須), lang?, input_mode?, message_text?, audio(UploadFile)?
    データは Celery のオーケストレータタスク（TASK_ORCHESTRATE_CONVERSATION）に委譲する。
    応答の最終結果は /api/v1/sessions/restore にてポーリング取得。
    """
    # Content-Type 判定
    content_type = request.headers.get("content-type", "").lower()
    if "multipart/form-data" in content_type:
        parsed = await _read_multipart(request)
    elif "application/json" in content_type or content_type == "":
        # content-type が空でも JSON を試みる（クライアント実装差分に寛容）
        parsed = await _read_json(request)
    else:
        raise HTTPException(status_code=415, detail=f"未対応の Content-Type です: {content_type}")

    # ユーザーIDは任意。未ログイン運用も許容。
    user_id = current_user.get("id") if current_user else None

    payload = {
        "session_id": parsed["session_id"],
        "user_id": user_id,
        "lang": parsed["lang"],
        "input_mode": parsed["input_mode"],  # "text" or "voice"
        "message_text": parsed.get("message_text"),
        "audio_b64": parsed.get("audio_b64"),
    }

    try:
        # Celery にディスパッチ（タスク名は shared.app.tasks の定義に一致させる）
        async_result = celery_app.send_task(TASK_ORCHESTRATE_CONVERSATION, args=[payload])
        # 202 Accepted で task_id を返す（結果は DB ポーリングで復元）
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "ok": True,
                "task_id": async_result.id,
                "queued": True,
                "session_id": parsed["session_id"],
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"タスクディスパッチに失敗しました: {e}")
