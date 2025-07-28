#backend/api_gateway_app
from fastapi import APIRouter, HTTPException, status, Body
from celery.result import AsyncResult
from typing import Any, Dict
from pydantic import BaseModel

from backend.shared.celery_app import celery_app
from backend.shared.schemas import ChatRequest, UpdateLocationRequest

router = APIRouter()

class TaskResponse(BaseModel):
    task_id: str

class TaskStatusResponse(BaseModel):
    task_id: str
    status: str
    result: Any = None

@router.post("/chat", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def chat_endpoint(request: ChatRequest):
    """ユーザーからのチャットリクエストを受け付け、ワーカーに処理を依頼"""
    task = celery_app.send_task("app.tasks.process_chat_message", args=[request.model_dump()])
    return {"task_id": task.id}

@router.post("/update_location", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def update_location_endpoint(request: UpdateLocationRequest):
    """ユーザーの位置情報更新を受け付け、ワーカーに処理を依頼"""
    task = celery_app.send_task("app.tasks.process_location_update", args=[request.model_dump()])
    return {"task_id": task.id}

@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """タスクIDを指定して、非同期処理の状況や結果を取得します。"""
    task_result = AsyncResult(task_id, app=celery_app)
    
    if not task_result:
        raise HTTPException(status_code=404, detail="Task ID not found.")

    response_data = {"task_id": task_id, "status": task_result.state, "result": None}
    if task_result.ready():
        if task_result.successful():
            response_data["result"] = task_result.get()
        else:
            response_data["result"] = str(task_result.info)
    return TaskStatusResponse(**response_data)