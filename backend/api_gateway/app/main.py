# -*- coding: utf-8 -*-
"""
FastAPI アプリケーションのエントリポイント。
- CORS 設定
- v1 ルーター群の登録（auth / sessions / chat）
- /health の登録（最初に登録しておくと疎通確認が容易）
"""

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ルーターの取り込み（PYTHONPATH=backend 前提）
from api_gateway.app.health import router as health_router
from api_gateway.app.api.v1.auth import router as auth_router
from api_gateway.app.api.v1.sessions import router as sessions_router
from api_gateway.app.api.v1.chat import router as chat_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Guidance LLM API Gateway",
        version=os.getenv("APP_VERSION", "0.1.0"),
        docs_url=os.getenv("DOCS_URL", "/docs"),
        redoc_url=os.getenv("REDOC_URL", "/redoc"),
        openapi_url=os.getenv("OPENAPI_URL", "/openapi.json"),
    )

    # CORS 設定（必要に応じて .env から許可オリジンを読み込む）
    allow_origins = os.getenv("CORS_ALLOW_ORIGINS", "*")
    allow_credentials = os.getenv("CORS_ALLOW_CREDENTIALS", "true").lower() == "true"
    allow_methods = os.getenv("CORS_ALLOW_METHODS", "*")
    allow_headers = os.getenv("CORS_ALLOW_HEADERS", "*")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in allow_origins.split(",")] if allow_origins != "*" else ["*"],
        allow_credentials=allow_credentials,
        allow_methods=[m.strip() for m in allow_methods.split(",")] if allow_methods != "*" else ["*"],
        allow_headers=[h.strip() for h in allow_headers.split(",")] if allow_headers != "*" else ["*"],
    )

    # ルーター登録
    # /health は最初に
    app.include_router(health_router)  # => GET /health

    # 各 v1 ルーターは **相対パス（/auth, /sessions, /chat）** を前提に、
    # ここで prefix="/api/v1" を付与して公開 URL を /api/v1/... に統一する。
    app.include_router(auth_router, prefix="/api/v1")      # => /api/v1/auth/...
    app.include_router(sessions_router, prefix="/api/v1")  # => /api/v1/sessions/...
    app.include_router(chat_router, prefix="/api/v1")      # => /api/v1/chat/...

    # ルート（任意）：サービス概要
    @app.get("/")
    async def root() -> dict:
        return {
            "service": "api-gateway",
            "version": app.version,
            "endpoints": ["/health", "/api/v1/auth/*", "/api/v1/sessions/*", "/api/v1/chat/*"],
        }

    return app


app = create_app()
