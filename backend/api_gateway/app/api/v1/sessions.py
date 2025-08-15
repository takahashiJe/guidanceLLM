# backend/api_gateway/app/api/v1/sessions.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from shared.app.database import get_db
from shared.app import models, schemas
from api_gateway.app.security import get_current_user

router = APIRouter()

@router.post("/create", response_model=schemas.SessionCreateResponse)
def create_session(payload: schemas.SessionCreateRequest,
                   db: Session = Depends(get_db),
                   user: models.User = Depends(get_current_user)):
    # 既存の同一IDは上書きせずエラー（フロントがUUID生成想定のため）
    exists = db.query(models.Session).filter(models.Session.id == payload.session_id).first()
    if exists:
        raise HTTPException(status_code=400, detail="session_id already exists")
    sess = models.Session(
        id=payload.session_id,
        user_id=user.id,
        current_status="Browse",
        active_plan_id=None,
        language=payload.language or user.preferred_language or "ja",
        dialogue_mode=payload.dialogue_mode or "text",
    )
    db.add(sess)
    db.commit()
    return schemas.SessionCreateResponse(session_id=sess.id, current_status=sess.current_status)

@router.get("/restore/{session_id}", response_model=schemas.SessionRestoreResponse)
def restore_session(session_id: str,
                    db: Session = Depends(get_db),
                    user: models.User = Depends(get_current_user)):
    sess = db.query(models.Session).filter(
        models.Session.id == session_id, models.Session.user_id == user.id
    ).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    # 直近 N 件の履歴（前後・SYSTEM_TRIGGER含む）
    messages = (
        db.query(models.ConversationMessage)
        .filter(models.ConversationMessage.session_id == session_id)
        .order_by(models.ConversationMessage.created_at.asc())
        .all()
    )
    history = [
        schemas.ChatMessage(
            role=m.role, content=m.content, created_at=m.created_at, meta=m.meta
        )
        for m in messages
    ]
    return schemas.SessionRestoreResponse(
        session_id=sess.id,
        current_status=sess.current_status,
        active_plan_id=sess.active_plan_id,
        language=sess.language,
        dialogue_mode=sess.dialogue_mode,
        history=history,
    )
