# backend/api_gateway/app/api/v1/chat.py

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from typing import Optional
import uuid
from sqlalchemy.orm import Session

from shared.app.tasks import (
    orchestrate_conversation_task,
    start_navigation_task,
    update_location_task
)
from shared.app import models, schemas
from shared.app.database import get_db
from api_gateway.app.security import get_current_user

router = APIRouter()

@router.post("/message", status_code=status.HTTP_202_ACCEPTED, summary="Post a user message (text or audio)")
def post_message(
    session_id: uuid.UUID = Form(...),
    text: Optional[str] = Form(None),
    audio_file: Optional[UploadFile] = File(None),
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """FR-2, FR-7: ユーザーからのメッセージを受け付け、Workerに対話処理タスクを投入します。"""
    if not text and not audio_file:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Either text or audio_file must be provided")

    # [認可] このセッションが本当にこのユーザーのものであるか検証
    session = db.query(models.Session).filter(models.Session.session_id == session_id).first()
    if not session or session.user_id != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this session")

    audio_data = audio_file.file.read() if audio_file else None
    
    # Celeryブローカーがダウンしている場合、CeleryErrorが発生する可能性がある
    # これはmain.pyのグローバルハンドラで捕捉される
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
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """FR-5: ナビゲーション開始のトリガー。"""
    # [認可] この計画が本当にこのユーザーのものであるか検証
    plan = db.query(models.Plan).filter(models.Plan.plan_id == navigation_start.plan_id).first()
    if not plan or plan.user_id != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to start navigation for this plan")
    
    start_navigation_task.delay(
        session_id=str(navigation_start.session_id),
        plan_id=navigation_start.plan_id, 
        user_id=current_user.user_id
    )
    return {"message": "Navigation start request accepted."}


@router.post("/navigation/location", status_code=status.HTTP_202_ACCEPTED, summary="Update user location during navigation")
def update_location(
    location_data: schemas.LocationData,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """FR-5-3: ナビ中の位置情報更新。"""
    # [認可] このセッションが本当にこのユーザーのものであるか検証 (頻繁な呼び出しのため省略も検討)
    session = db.query(models.Session).filter(models.Session.session_id == location_data.session_id).first()
    if not session or session.user_id != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to update location for this session")

    update_location_task.delay(
        session_id=str(location_data.session_id),
        user_id=current_user.user_id,
        latitude=location_data.latitude,
        longitude=location_data.longitude
    )
    return {"message": "Location update accepted."}
