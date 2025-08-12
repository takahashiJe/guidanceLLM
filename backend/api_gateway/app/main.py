# backend/api_gateway/app/main.py

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError
from celery.exceptions import CeleryError
import logging

# api/v1ディレクトリ内の各ルーターをインポート
from api_gateway.app.api.v1 import auth, sessions, chat

# ロガーの設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Guidance LLM API Gateway",
    description="This is the main entry point for the Guidance LLM application.",
    version="1.0.0",
)

# --- グローバル例外ハンドラ ---
@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
    """データベース関連のエラーを一元的に捕捉する。"""
    logger.error(f"Database error occurred for request {request.method} {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Database service is currently unavailable. Please try again later."},
    )

@app.exception_handler(CeleryError)
async def celery_exception_handler(request: Request, exc: CeleryError):
    """Celeryタスク投入時のエラーを一元的に捕捉する。"""
    logger.error(f"Celery task dispatch error for request {request.method} {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Background processing service is currently unavailable. Please try again later."},
    )

# --- ミドルウェアの設定 ---
origins = [
    "http://localhost",
    "http://localhost:8080",
    "http://localhost:8501",
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
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(sessions.router, prefix="/api/v1/sessions", tags=["Sessions"])
app.include_router(chat.router, prefix="/api/v1/chat", tags=["Chat & Navigation"])


@app.get("/")
def read_root():
    return {"message": "Welcome to the Guidance LLM API Gateway"}
