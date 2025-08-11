# backend/api_gateway/app/api/v1/chat.py

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from typing import Optional
import uuid

# backend/shared/app/tasks.py で定義されたCeleryタスクのシグネチャをインポート
from backend.shared.app.tasks import (
    orchestrate_conversation_task,
    start_navigation_task,
    update_location_task
)

# 共通のモデル、スキーマ、認証ヘルパーをインポート
from backend.shared.app import models, schemas
from backend.api_gateway.app.security import get_current_user

router = APIRouter()

@router.post("/message", status_code=status.HTTP_202_ACCEPTED, summary="Post a user message (text or audio)")
def post_message(
    session_id: uuid.UUID = Form(...),
    text: Optional[str] = Form(None),
    audio_file: Optional[UploadFile] = File(None),
    current_user: models.User = Depends(get_current_user)
):
    """
    FR-2, FR-7: ユーザーからのメッセージ（テキストor音声）を受け付け、
    Workerに対話処理タスクを投入します。
    """
    if not text and not audio_file:
        raise HTTPException(status_code=400, detail="Either text or audio_file must be provided")

    # 本番環境では、音声ファイルはS3などのオブジェクトストレージにアップロードし、
    # そのURLやキーをタスクに渡すのが一般的です。
    # ここではPoCとしてファイル内容を直接渡します。
    audio_data = audio_file.file.read() if audio_file else None
    
    # 対話オーケストレーションを実行するCeleryタスクを非同期で呼び出す
    orchestrate_conversation_task.delay(
        session_id=str(session_id),
        user_id=current_user.user_id,
        text=text,
        audio_data=audio_data
    )
    
    return {"message": "Request accepted, processing in background."}


@router.post("/navigation/start", status_code=status.HTTP_202_ACCEPTED, summary="Start navigation for a plan")
def start_navigation(
    navigation_start: schemas.NavigationStart,
    current_user: models.User = Depends(get_current_user)
):
    """
    FR-5: ナビゲーション開始のトリガー。
    指定された計画IDに基づき、ナビゲーションセッションを開始するためのタスクを投入します。
    """
    # TODO: 本番実装では、リクエストされたplan_idが本当にcurrent_userのものであるか、
    # データベースをチェックする認可処理を追加することが望ましい。
    
    start_navigation_task.delay(
        session_id=str(navigation_start.session_id),
        plan_id=navigation_start.plan_id, 
        user_id=current_user.user_id
    )
    return {"message": "Navigation start request accepted."}


@router.post("/navigation/location", status_code=status.HTTP_202_ACCEPTED, summary="Update user location during navigation")
def update_location(
    location_data: schemas.LocationData,
    current_user: models.User = Depends(get_current_user)
):
    """
    FR-5-3: ナビ中の位置情報更新。
    フロントエンドから定期的に送られてくる位置情報を受け取り、イベント検知タスクを投入します。
    """
    update_location_task.delay(
        session_id=str(location_data.session_id),
        user_id=current_user.user_id,
        latitude=location_data.latitude,
        longitude=location_data.longitude
    )
    return {"message": "Location update accepted."}
