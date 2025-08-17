# -*- coding: utf-8 -*-
"""
セッション管理 API
- /api/v1/sessions/create : セッション作成（body 任意、session_id 未指定ならサーバで生成）
  - 既存の session_id が指定された場合は「初期化（状態リセット）」を行う
    * ConversationHistory / PreGeneratedGuides をクリア
    * active_plan_id = NULL
    * current_status は DB デフォルトに任せる（明示更新しない）
  - 既存が無ければ新規作成
- /api/v1/sessions/restore/{session_id} : セッション復元

注意:
- ルーターには prefix を付けない（/api/v1/sessions は main.py 側で付与）
"""

from __future__ import annotations

import uuid
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status, Body
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.sql.sqltypes import Integer as SAInteger

from shared.app.database import get_db
from shared.app import models
from api_gateway.app.security import get_current_user

router = APIRouter(prefix="/sessions")

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
    # テストは camelCase の appStatus を期待するため、エイリアスで返す
    app_status: str = Field(..., serialization_alias="appStatus")
    active_plan_id: Optional[int] = Field(None, serialization_alias="activePlanId")

    model_config = {
        "populate_by_name": True,
        "from_attributes": True,
    }


# -----------------------------
# 内部 util
# -----------------------------
def _load_history(db: Session, session_rec: models.Session, limit: int = 10) -> List[Dict[str, Any]]:
    """
    会話履歴を新しい順に取り、返却は古い→新しいの時系列に整えて返す。
    ConversationHistory.session_id が Integer / String どちらのスキーマでも動くように、
    カラム型を見て join キーを切り替える。
    """
    # どの値で比較するかをカラム型から決める
    col = models.ConversationHistory.session_id
    key_value: Any = session_rec.id if isinstance(col.type, SAInteger) else session_rec.session_id

    rows = (
        db.query(models.ConversationHistory)
        .filter(col == key_value)
        .order_by(models.ConversationHistory.created_at.desc())
        .limit(limit)
        .all()
    )

    out: List[Dict[str, Any]] = []
    for h in reversed(rows):
        created_at = getattr(h, "created_at", None)
        out.append(
            {
                "role": getattr(h, "role", None) or "user",           # 例: "user" / "assistant"
                "type": getattr(h, "message_type", None) or "text",   # 例: "text" / "audio"
                "content": getattr(h, "content", None)
                or getattr(h, "message_text", None)
                or "",
                "createdAt": created_at.isoformat() if created_at else None,
            }
        )
    return out


# -----------------------------
# /create
# -----------------------------
@router.post("/create", response_model=SessionCreateResponse)
def create_session(
    payload: Optional[SessionCreateRequest] = Body(None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    - body 任意。payload が None でも受ける。
    - session_id 未指定ならサーバで UUID を払い出し。
    - 指定された session_id が既に存在する場合は「既存セッションの初期化（状態リセット）」を行う。
      * ConversationHistory を削除
      * PreGeneratedGuides を削除（モデルがある場合のみ）
      * active_plan_id = NULL
      * current_status は DB デフォルト/現在値に任せる
      * 200 OK を返す
    - 新規作成の場合は 201 Created を返す
    """
    if payload is None:
        payload = SessionCreateRequest()

    session_id = payload.session_id or uuid.uuid4().hex

    # ここは整数PKの id ではなく、文字列の session_id で検索する
    found = (
        db.query(models.Session)
        .filter(models.Session.session_id == session_id, models.Session.user_id == user.id)
        .first()
    )

    if found:
        # --- 既存セッションの初期化（状態リセット） ---
        db.query(models.ConversationHistory).filter(
            models.ConversationHistory.session_id
            == (found.id if isinstance(models.ConversationHistory.session_id.type, SAInteger) else found.session_id)
        ).delete(synchronize_session=False)

        if hasattr(models, "PreGeneratedGuide"):
            db.query(getattr(models, "PreGeneratedGuide")).filter(
                getattr(models, "PreGeneratedGuide").session_id == session_id
            ).delete(synchronize_session=False)

        found.active_plan_id = None
        db.add(found)
        db.commit()
        db.refresh(found)

        return SessionCreateResponse(
            session_id=found.session_id,
            app_status=found.current_status or "idle",
            active_plan_id=found.active_plan_id,
            reset=True,
        )

    # --- 新規作成 ---
    rec = models.Session(
        session_id=session_id,
        user_id=user.id,
        active_plan_id=None,
        # current_status は指定しない（DB デフォルトに任せる）
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    return Response(
        content=SessionCreateResponse(
            session_id=rec.session_id,
            app_status=rec.current_status or "idle",
            active_plan_id=rec.active_plan_id,
            reset=False,
        ).model_dump_json(),
        media_type="application/json",
        status_code=status.HTTP_201_CREATED,
    )


# -----------------------------
# /restore/{session_id}
# -----------------------------
@router.get("/restore/{session_id}")
def restore_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    rec = (
        db.query(models.Session)
        .filter(models.Session.session_id == session_id, models.Session.user_id == user.id)
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="session not found")

    app_status = rec.current_status or "idle"
    active_plan_id = rec.active_plan_id
    history = _load_history(db, session_rec=rec, limit=10)

    # 互換のため camel / snake を併記
    return {
        "session_id": rec.session_id,
        "appStatus": app_status,
        "activePlanId": active_plan_id,
        "active_plan_id": active_plan_id,
        "history": history,
    }