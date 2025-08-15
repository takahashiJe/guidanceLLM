# backend/api_gateway/app/main.py
# FastAPI アプリのエントリーポイント。CORS、ルーター登録、基本ヘルスチェックなど。

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api_gateway.app.api.v1 import auth as auth_router
from api_gateway.app.api.v1 import sessions as sessions_router
from api_gateway.app.api.v1 import chat as chat_router

APP_TITLE = "Chokai Guidance API Gateway"
APP_VERSION = "v1"

def create_app() -> FastAPI:
    app = FastAPI(title=APP_TITLE, version=APP_VERSION)

    # CORS 設定（必要に応じて .env で制御してもOK）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # 本番は必要最小限のオリジンに絞る
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    # v1 ルーター登録
    app.include_router(auth_router.router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(sessions_router.router, prefix="/api/v1/sessions", tags=["sessions"])
    app.include_router(chat_router.router, prefix="/api/v1", tags=["chat"])

    @app.get("/healthz", tags=["health"])
    def healthz():
        return {"status": "ok"}

    return app

app = create_app()
