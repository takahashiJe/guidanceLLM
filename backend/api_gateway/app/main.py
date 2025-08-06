# backend/api_gateway/app/main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# api/v1ディレクトリ内の各ルーターをインポート
from backend.api_gateway.app.api.v1 import auth, sessions, chat

app = FastAPI(
    title="Guidance LLM API Gateway",
    description="This is the main entry point for the Guidance LLM application.",
    version="1.0.0",
)

# --- ミドルウェアの設定 ---
# フロントエンドからのリクエストを許可する
# 本番環境では、オリジンをより厳密に設定すること
origins = [
    "http://localhost",
    "http://localhost:8080", # Vue開発サーバーのデフォルトポートなど
    "http://localhost:8501", # Streamlitのデフォルトポート
    "https://ibera.cps.akita-pu.ac.jp"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- APIルーターのインクルード ---
# 各エンドポイントを有効化する
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(sessions.router, prefix="/api/v1/sessions", tags=["Sessions"])
app.include_router(chat.router, prefix="/api/v1/chat", tags=["Chat & Navigation"])


@app.get("/")
def read_root():
    return {"message": "Welcome to the Guidance LLM API Gateway"}
