# -*- coding: utf-8 -*-
"""
セッション管理 API
- /api/v1/sessions/create : セッション作成（body 任意、session_id 未指定ならサーバで生成）
  - 既存の session_id が指定された場合は「初期化（状態リセット）」を行う
    * app_status = "idle"
    * active_plan_id = NULL
    * ConversationHistory / PreGeneratedGuides をクリア
  - 既存が無ければ新規作成
- /api/v1/sessions/restore/{session_id} : セッション復元

注意:
- ルーターには prefix を付けない（/api/v1/sessions は main.py 側で付与）
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status, Body
from pydantic import BaseModel
from sqlalchemy.orm import Session

from shared.app.database import get_db
from shared.app import models
from api_gateway.app.security import get_current_user

# ★ prefix は付けない（main.py 側で /api/v1/sessions を付与）
router = APIRouter(tags=["sessions"])


# -----------------------------
# モデル
# -----------------------------
class SessionCreateRequest(BaseModel):
    # 任意。未指定ならサーバで UUID を払い出す
    session_id: Optional[str] = None


class SessionCreateResponse(BaseModel):
    session_id: str
    app_status: str
    active_plan_id: Optional[int] = None
    # 既存セッションを初期化したかどうか（新規作成時は False）
    reset: bool = False


class SessionRestoreResponse(BaseModel):
    session_id: str
    app_status: str
    active_plan_id: Optional[int] = None


# -----------------------------
# /create
# -----------------------------
@router.post("/create", response_model=SessionCreateResponse)
def create_session(
    # Body(None) にすることで「body なし」も受け付ける（FastAPI の 422 を回避）
    payload: Optional[SessionCreateRequest] = Body(None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    - body 任意。payload が None でも受ける。
    - session_id 未指定ならサーバで UUID を払い出し。
    - 指定された session_id が既に存在する場合は「既存セッションの初期化（状態リセット）」を行う。
      * ConversationHistory を削除
      * PreGeneratedGuides を削除（存在する場合のみ）
      * app_status = 'idle', active_plan_id = NULL
      * 200 OK を返す
    - 新規作成の場合は 201 Created を返す
    """
    if payload is None:
        payload = SessionCreateRequest()

    session_id = payload.session_id or uuid.uuid4().hex

    # 自分のセッションのみ対象
    found = (
        db.query(models.Session)
        .filter(models.Session.id == session_id, models.Session.user_id == user.id)
        .first()
    )

    if found:
        # --- 既存セッションの初期化（状態リセット） ---
        # 会話履歴の削除
        db.query(models.ConversationHistory).filter(
            models.ConversationHistory.session_id == session_id
        ).delete(synchronize_session=False)

        # 事前生成ガイドの削除（モデルが存在する場合のみ）
        if hasattr(models, "PreGeneratedGuide"):
            db.query(getattr(models, "PreGeneratedGuide")).filter(
                getattr(models, "PreGeneratedGuide").session_id == session_id
            ).delete(synchronize_session=False)

        # セッション状態の初期化
        found.app_status = "idle"
        found.active_plan_id = None
        db.add(found)
        db.commit()

        return SessionCreateResponse(
            session_id=found.id,
            app_status=found.app_status or "idle",
            active_plan_id=found.active_plan_id,
            reset=True,
        )

    # --- 新規作成 ---
    rec = models.Session(
        id=session_id,
        user_id=user.id,
        app_status="idle",
        active_plan_id=None,
    )
    db.add(rec)
    db.commit()

    # 201 Created を明示
    return Response(
        content=SessionCreateResponse(
            session_id=session_id, app_status="idle", active_plan_id=None, reset=False
        ).model_dump_json(),
        media_type="application/json",
        status_code=status.HTTP_201_CREATED,
    )


# -----------------------------
# /restore/{session_id}
# -----------------------------
@router.get("/restore/{session_id}", response_model=SessionRestoreResponse)
def restore_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    rec = (
        db.query(models.Session)
        .filter(models.Session.id == session_id, models.Session.user_id == user.id)
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="session not found")

    return SessionRestoreResponse(
        session_id=rec.id,
        app_status=rec.app_status or "idle",
        active_plan_id=rec.active_plan_id,
    )
