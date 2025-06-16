from fastapi import FastAPI
from api_gateway.chat_router import router

# FastAPIアプリケーションのインスタンスを作成
app = FastAPI(
    title="山道案内AI API Gateway",
    description="ユーザーからのリクエストを受け付け、ワーカーに処理を依頼するAPIです。",
    version="1.0.0",
)

# /api/v1 というプレフィックスでチャットルーターを登録します。
# これにより、このルーター内のエンドポイントはすべて /api/v1/chat のようになります。
app.include_router(router, prefix="/api/v1", tags=["Task Endpoints"])

@app.get("/", tags=["Root"])
async def read_root():
    """
    APIサーバーが正常に起動しているかを確認するためのルートエンドポイントです。
    """
    return {"message": "API Gateway is running."}