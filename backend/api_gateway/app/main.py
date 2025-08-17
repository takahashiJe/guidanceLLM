# -*- coding: utf-8 -*-
"""
FastAPI エントリポイント（API Gateway）
- /api/v1 は main 側で一度だけ付与
- 各モジュールは /auth/* /sessions/* /chat/* /navigation/* を自身のルーターで定義
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ルーターの取り込み（各モジュールは自分のサブパスのみ定義）
from api_gateway.app.api.v1.auth import router as auth_router
from api_gateway.app.api.v1.sessions import router as sessions_router
from api_gateway.app.api.v1.chat import router as chat_router
from api_gateway.app.health import router as health_router


def create_app() -> FastAPI:
    app = FastAPI(title="Guidance LLM API Gateway", version="1.0.0")

    # CORS 設定（必要に応じて調整）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    # /api/v1 は main 側で付与
    app.include_router(auth_router, prefix="/api/v1", tags=["auth"])
    app.include_router(sessions_router, prefix="/api/v1", tags=["sessions"])
    app.include_router(chat_router, prefix="/api/v1", tags=["chat", "navigation"])

    # /health はそのまま
    app.include_router(health_router, tags=["health"])

    @app.get("/")
    def root():
        # 既存の想定に合わせて最小限の情報を返却
        return {"ok": True, "service": "api-gateway"}

    return app


app = create_app()
