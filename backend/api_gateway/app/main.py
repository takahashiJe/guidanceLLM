# backend/api_gateway/app/main.py
# -*- coding: utf-8 -*-
"""
API Gateway エントリポイント

ポリシー:
- /api/v1 のプレフィックスは main.py 側で付与する
- 各モジュール（auth/sessions/chat など）はサブパスのみを定義（例: /auth, /sessions, /chat）
- /health はルート直下（/health）に公開
- 追加の /navigation/start も /api/v1 配下に載せる
"""

from __future__ import annotations

from fastapi import FastAPI, APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

# 既存ルーター（サブパスのみ定義されている想定）
from api_gateway.app.health import router as health_router
from api_gateway.app.api.v1.auth import router as auth_router
from api_gateway.app.api.v1.sessions import router as sessions_router
from api_gateway.app.api.v1.chat import router as chat_router, nav_router as navigation_router

# セキュリティ（任意認証）
from api_gateway.app.security import get_current_user_optional

# Celery
from shared.app.celery_app import celery_app
from shared.app.tasks import TASK_START_NAVIGATION

app = FastAPI(title="Guidance LLM API", version="1.0.0")

# /health はルート直下
app.include_router(health_router)

# /api/v1 配下に各ルーターをマウント
app.include_router(health_router)
app.include_router(auth_router, prefix="/api/v1/auth")
app.include_router(sessions_router, prefix="/api/v1/sessions")
app.include_router(chat_router)          # /api/v1/chat/...
app.include_router(navigation_router)    # /api/v1/navigation/...

# ------------------------------------------------------------
# 追加: Navigation Start （/api/v1/navigation/start）
# ------------------------------------------------------------
nav_router = APIRouter(prefix="/navigation", tags=["navigation"])

@nav_router.post("/start")
async def navigation_start(
    payload: dict,
    current_user: dict | None = Depends(get_current_user_optional),
):
    """
    ナビ開始を非同期でトリガー。
    入力: { "session_id": str, "lang": "ja"|"en"|"zh" }
    返却: 202 + {accepted: true, task_id: "..."}
    """
    session_id = (payload.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id は必須です。")

    lang = (payload.get("lang") or "ja").strip()
    user_id = current_user["user_id"] if current_user else None

    task_payload = {"session_id": session_id, "user_id": user_id, "lang": lang}
    async_result = celery_app.send_task(TASK_START_NAVIGATION, args=[task_payload])
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"accepted": True, "task_id": async_result.id},
    )

# /api/v1 配下にマウント
app.include_router(nav_router, prefix="/api/v1")
