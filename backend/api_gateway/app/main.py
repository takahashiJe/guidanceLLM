# backend/api_gateway/app/main.py
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI

from api_gateway.app.api.v1 import auth as auth_router
from api_gateway.app.api.v1 import sessions as sessions_router
from api_gateway.app.api.v1 import chat as chat_router
from api_gateway.app.health import router as health_router

APP_TITLE = "Chokai Guidance API Gateway"
APP_VERSION = "v1"


def create_app() -> FastAPI:
    app = FastAPI(title=APP_TITLE, version=APP_VERSION)

    # CORS（必要に応じて許可オリジンを絞る）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    # v1 ルーター登録
    app.include_router(auth_router.router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(sessions_router.router, prefix="/api/v1/sessions", tags=["sessions"])
    app.include_router(chat_router.router, prefix="/api/v1", tags=["chat"])
    app.include_router(health_router)  # /api/v1/healthz, /api/v1/healthz/db

    # 互換の簡易ヘルス（旧 /healthz）。必要なら残す。
    @app.get("/healthz", tags=["health"])
    def healthz():
        return {"status": "ok"}

    return app

app = create_app()