#backend/api_gateway_app
from fastapi import APIRouter, HTTPException, status
from celery.result import AsyncResult
from typing import Any, Dict
from pydantic import BaseModel

# 共有ディレクトリからCeleryインスタンスとPydanticスキーマをインポート
from shared.celery_app import celery_app
from shared.schemas import ChatRequest, UpdateLocationRequest, ChatResponse, UpdateLocationResponse

# APIRouterのインスタンスを作成
router = APIRouter()


class TaskStatusResponse(BaseModel):
    """タスクの状況を返すためのレスポンススキーマ"""
    task_id: str
    status: str
    result: Any = None


@router.post("/chat", response_model=Dict[str, str], status_code=status.HTTP_202_ACCEPTED)
async def chat_endpoint(request: ChatRequest):
    """
    ユーザーからのチャットリクエストを受け付け、ワーカーに処理を依頼します。
    このエンドポイントは重い処理を待たずに、すぐにタスクIDを返します。
    """
    # Celeryタスクを非同期で呼び出します。
    # `send_task`を使い、タスク名を文字列で指定することでworkerとの疎結合を保ちます。
    # `args`には、シリアライズ可能なデータ（辞書）を渡します。
    task = celery_app.send_task("process_chat_message", args=[request.model_dump()])
    
    # すぐにタスクIDを返すことで、クライアントは結果をポーリングできます。
    return {"task_id": task.id}


@router.post("/update_location", response_model=Dict[str, str], status_code=status.HTTP_202_ACCEPTED)
async def update_location_endpoint(request: UpdateLocationRequest):
    """
    ユーザーの位置情報更新を受け付け、ワーカーに処理を依頼します。
    """
    task = celery_app.send_task("process_location_update", args=[request.model_dump()])
    return {"task_id": task.id}


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """
    タスクIDを指定して、非同期処理の状況や結果を取得します。
    フロントエンドは、このエンドポイントを定期的にポーリングします。
    """
    # CeleryからタスクIDに対応する結果オブジェクトを取得
    task_result = AsyncResult(task_id, app=celery_app)
    
    if not task_result:
        raise HTTPException(status_code=404, detail="Task ID not found.")

    response_data = {
        "task_id": task_id,
        "status": task_result.state,
        "result": None
    }

    if task_result.ready():
        # タスクが完了している場合
        if task_result.successful():
            # 成功した場合、結果を取得
            response_data["result"] = task_result.get()
        else:
            # タスクが失敗した場合、エラー情報を取得
            # 本番環境では、より詳細なエラーハンドリングを検討します。
            response_data["result"] = str(task_result.info)

    return TaskStatusResponse(**response_data)