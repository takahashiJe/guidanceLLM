# backend/api_gateway/app/api/v1/sessions.py

import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

# sharedディレクトリと、同じ階層のsecurity.pyからインポート
from backend.shared.app.database import get_db
from backend.shared.app import models, schemas
from backend.api_gateway.app.security import get_current_user

router = APIRouter()

@router.post("/create", response_model=schemas.SessionResponse)
def create_session(session_create: schemas.SessionCreate, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """
    FR-1-2: 新しい会話セッションを作成する
    """
    # リクエスト元のユーザーと、セッションを作成しようとしているユーザーが一致するか確認
    if current_user.user_id != session_create.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to create session for this user")

    db_session = models.Session(
        session_id=session_create.session_id,
        user_id=session_create.user_id,
        app_status='browsing',
        language=session_create.language,
        interaction_mode=session_create.interaction_mode
    )
    db.add(db_session)
    db.commit()
    db.refresh(db_session)
    
    return db_session

@router.get("/restore/{session_id}", response_model=schemas.SessionRestoreResponse)
def restore_session(session_id: uuid.UUID, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """
    FR-1-3: 既存セッションの状態と会話履歴を復元する
    """
    db_session = db.query(models.Session).filter(models.Session.session_id == session_id).first()
    if not db_session:
        raise HTTPException(status_code=404, detail="Session not found")

    # 自分のセッション以外は復元できないようにする
    if db_session.user_id != current_user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to access this session")

    history = db.query(models.ConversationHistory).filter(models.ConversationHistory.session_id == session_id).order_by(models.ConversationHistory.turn).all()

    return {
        "session_id": db_session.session_id,
        "user_id": db_session.user_id,
        "app_status": db_session.app_status,
        "active_plan_id": db_session.active_plan_id,
        "language": db_session.language,
        "interaction_mode": db_session.interaction_mode,
        "history": history
    }
