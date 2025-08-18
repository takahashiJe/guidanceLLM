# -*- coding: utf-8 -*-
"""
シンプルなヘルスチェック用エンドポイント。
- 依存先に負荷をかけない軽量チェック（ルーティング登録の確認が主目的）
- /health: 200 OK を返す
"""

from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["/health"])

@router.get("/")
async def health() -> dict:
    # ここでは「FastAPI が起動し、ルーターが正しく登録されている」ことのみを確認する。
    # 依存コンポーネント（DB/Celery/OSRM等）の詳細チェックは別の /healthz 等で行うのが安全。
    return {
        "status": "ok",
        "service": "api-gateway",
    }
