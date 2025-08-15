# backend/api_gateway/app/api/v1/chat.py
from typing import Optional
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.orm import Session

from shared.app.database import get_db
from shared.app import models, schemas, tasks as shared_tasks
from api_gateway.app.security import get_current_user

router = APIRouter()

@router.post("/chat/message", response_model=schemas.TaskAcceptedResponse, status_code=202)
async def post_message(
    session_id: str = Form(...),
    text: Optional[str] = Form(None),
    language: Optional[str] = Form(None),
    dialogue_mode: Optional[str] = Form(None),  # "text" or "voice"
    audio_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    sess = db.query(models.Session).filter(
        models.Session.id == session_id, models.Session.user_id == user.id
    ).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    if not text and not audio_file:
        raise HTTPException(status_code=400, detail="Either text or audio_file is required")

    payload = {
        "session_id": session_id,
        "user_id": user.id,
        "language": language or sess.language or user.preferred_language or "ja",
        "dialogue_mode": dialogue_mode or sess.dialogue_mode or "text",
        "text": text,
        "audio_filename": None,
        "audio_bytes_b64": None,
    }

    if audio_file:
        b = await audio_file.read()
        import base64
        payload["audio_filename"] = audio_file.filename
        payload["audio_bytes_b64"] = base64.b64encode(b).decode("utf-8")

    # 重い処理は Celery に委譲
    task_id = shared_tasks.dispatch_orchestrate_conversation(payload)
    return schemas.TaskAcceptedResponse(task_id=task_id)

@router.post("/navigation/start", response_model=schemas.TaskAcceptedResponse, status_code=202)
def start_navigation(
    payload: schemas.NavigationStartRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    sess = db.query(models.Session).filter(
        models.Session.id == payload.session_id, models.Session.user_id == user.id
    ).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    task_id = shared_tasks.dispatch_start_navigation({"session_id": payload.session_id, "user_id": user.id})
    return schemas.TaskAcceptedResponse(task_id=task_id)

@router.post("/navigation/location", response_model=schemas.TaskAcceptedResponse, status_code=202)
def update_location(
    payload: schemas.NavigationLocationUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    sess = db.query(models.Session).filter(
        models.Session.id == payload.session_id, models.Session.user_id == user.id
    ).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    task_id = shared_tasks.dispatch_update_location(payload.dict())
    return schemas.TaskAcceptedResponse(task_id=task_id)
