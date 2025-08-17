# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api_gateway.app.health import router as health_router
from api_gateway.app.api.v1.auth import router as auth_router
from api_gateway.app.api.v1.sessions import router as sessions_router
from api_gateway.app.api.v1.chat import router as chat_router
from api_gateway.app.api.v1.navigation import router as navigation_router  # ★追加

API_PREFIX = "/api/v1"

app = FastAPI(
    title="Guidance LLM API",
    version=os.getenv("API_VERSION", "0.1.0"),
)

# CORS（既存方針に合わせる）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 必要に応じて制限
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ルート
app.include_router(health_router, prefix="")
app.include_router(auth_router, prefix=API_PREFIX)
app.include_router(sessions_router, prefix=API_PREFIX)
app.include_router(chat_router, prefix=API_PREFIX)
app.include_router(navigation_router, prefix=API_PREFIX)  # ★追加

@app.get("/")
def root():
    # 既存の戻り値を壊さない（ヘルス/ルーティングのシンプル案内）
    return {"status": "ok", "message": "Guidance LLM API"}
