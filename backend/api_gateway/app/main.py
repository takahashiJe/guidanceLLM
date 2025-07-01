from fastapi import FastAPI
from app import chat_router
from fastapi.openapi.utils import get_openapi
from pydantic.v1.utils import deep_update
from pydantic.v1.openapi.utils import get_openapi as get_openapi_v1

# FastAPIアプリケーションのインスタンスを作成
app = FastAPI(
    title="山道案内AI API Gateway",
    description="ユーザーからのリクエストを受け付け、ワーカーに処理を依頼するAPIです。",
    version="1.0.0",
)

# /api/v1 というプレフィックスでチャットルーターを登録します。
# これにより、このルーター内のエンドポイントはすべて /api/v1/chat のようになります。
app.include_router(chat_router.router, prefix="/api/v1", tags=["Task Endpoints"])

@app.get("/", tags=["Root"])
async def read_root():
    """
    APIサーバーが正常に起動しているかを確認するためのルートエンドポイントです。
    """
    return {"message": "API Gateway is running."}

# Pydantic v1とv2の互換性問題を解決するためのカスタムopenapiメソッド
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    
    # v2のスキーマを生成
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    
    # v1のスキーマを生成
    openapi_schema_v1 = get_openapi_v1(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    
    # v1とv2のスキーマをマージする
    if "schemas" in openapi_schema_v1.get("components", {}):
        openapi_schema["components"]["schemas"] = deep_update(
            openapi_schema_v1["components"]["schemas"],
            openapi_schema.get("components", {}).get("schemas", {})
        )
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema
