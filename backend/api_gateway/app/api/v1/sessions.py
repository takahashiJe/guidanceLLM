# backend/api_gateway/app/api/v1/sessions.py
# セッションの作成 / 復元
# - フロント生成の session_id を受け入れ、Browse で初期化
# - 復元では appStatus / active_plan_id / 直近5往復(=10件)の履歴を返却
# - SYSTEM_TRIGGER を含む履歴も同様に返却（roleやcontentをそのまま）

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api_gateway.app.security import get_current_user
from shared.app.database import get_db
from shared.app.models import Session as SessionModel, ConversationHistory
from shared.app.schemas import (
    SessionCreateRequest,
    SessionCreateResponse,
    SessionRestoreResponse,
    ConversationItem,
)

router = APIRouter()


@router.post("/create", response_model=SessionCreateResponse)
def create_session(
    payload: SessionCreateRequest,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    # 既に存在すれば上書きしない（フロントがユニーク生成）
    existing = db.query(SessionModel).filter(SessionModel.id == payload.session_id).first()
    if existing:
        # 既存セッションがあっても、要件として「新規開始で計画リセット」が必要な場合はここで処理
        # ここでは current_status と active_plan_id を初期化
        existing.current_status = "Browse"
        existing.active_plan_id = None
        db.commit()
        return SessionCreateResponse(session_id=existing.id, current_status=existing.current_status, active_plan_id=existing.active_plan_id)

    new_session = SessionModel(
        id=payload.session_id,
        user_id=user.id,
        current_status="Browse",
        active_plan_id=None,
    )
    db.add(new_session)
    db.commit()
    return SessionCreateResponse(session_id=new_session.id, current_status=new_session.current_status, active_plan_id=new_session.active_plan_id)


@router.get("/restore/{session_id}", response_model=SessionRestoreResponse)
def restore_session(
    session_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    sess: Optional[SessionModel] = db.query(SessionModel).filter(
        SessionModel.id == session_id,
        SessionModel.user_id == user.id
    ).first()

    if not sess:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # 直近 5 往復 = 10 件（ユーザー/アシスタント/SYSTEM_TRIGGER 含む最新10件）
    history_rows: List[ConversationHistory] = (
        db.query(ConversationHistory)
        .filter(ConversationHistory.session_id == session_id)
        .order_by(ConversationHistory.created_at.desc())
        .limit(10)
        .all()
    )
    # 新しい順で取得しているので、フロントで扱いやすいように昇順へ
    history_rows.reverse()

    history: List[ConversationItem] = [
        ConversationItem(
            role=it.role,
            content=it.content,
            created_at=it.created_at.isoformat() if hasattr(it.created_at, "isoformat") else None,
        )
        for it in history_rows
    ]
    return SessionRestoreResponse(
        session_id=sess.id,
        current_status=sess.current_status,
        active_plan_id=sess.active_plan_id,
        history=history,
    )
