# backend/api_gateway/app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api_gateway.app.api.v1.auth import router as auth_router
from api_gateway.app.api.v1.sessions import router as sessions_router
from api_gateway.app.api.v1.chat import router as chat_router

app = FastAPI(title="Chokai Guide - API Gateway", version="1.0.0")

# CORS: 本番は環境変数や設定ファイルから制御するのが望ましい
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 開発中は *、本番は限定推奨
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ルーター登録
app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(sessions_router, prefix="/api/v1/sessions", tags=["sessions"])
app.include_router(chat_router, prefix="/api/v1", tags=["chat"])

@app.get("/health")
def health():
    return {"status": "ok"}
