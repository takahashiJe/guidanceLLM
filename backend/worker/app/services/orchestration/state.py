# -*- coding: utf-8 -*-
"""
state.py
- セッション状態のロード/保存（短期記憶=直近5往復、SYSTEM_TRIGGERの記録）
- LangGraph用の状態コンテナ（AgentState）の定義
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Literal
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from shared.app.database import get_session as get_db_session
from shared.app import models


# ============= LangGraph用の状態定義 =============
class HistoryMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    meta: Dict[str, Any] = Field(default_factory=dict)


class AgentState(BaseModel):
    session_id: str
    user_lang: Literal["ja", "en", "zh"] = "ja"
    app_status: Literal["Browse", "planning", "navigating"] = "Browse"
    active_plan_id: Optional[int] = None

    # 短期記憶（直近5往復=最大10メッセージ＋SYSTEM_TRIGGER）
    short_history: List[HistoryMessage] = Field(default_factory=list)

    # ユーザーの最新入力
    latest_user_message: Optional[str] = None

    # 最終応答（テキスト）
    final_response: Optional[str] = None

    # ルーティング結果用（情報/計画/雑談/END）
    route: Optional[str] = None

    # 付随データ（各ノード間の受け渡し）
    bag: Dict[str, Any] = Field(default_factory=dict)


# ============= 状態I/Oユーティリティ =============
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def load_state(session_id: str, limit_rounds: int = 5) -> AgentState:
    """
    DBからセッション状態を復元し、直近5往復（=10メッセージ）＋SYSTEM_TRIGGERを短期記憶として構築。
    """
    with get_db_session() as db:
        session = db.query(models.Session).filter(models.Session.session_id == session_id).one_or_none()
        if session is None:
            # ない場合は初期化
            return AgentState(session_id=session_id)

        # セッションメタ
        app_status = session.current_status or "Browse"
        active_plan_id = session.active_plan_id

        # 直近のメッセージ取得（SYSTEM_TRIGGER も role='system' として保存されている前提）
        # 最新から limit_rounds*2 件（user/assistantの往復）＋SYSTEM_TRIGGERを少し多めに取得
        q = (
            db.query(models.ConversationHistory)
            .filter(models.ConversationHistory.session_id == session_id)
            .order_by(models.ConversationHistory.created_at.desc())
            .limit(limit_rounds * 3)  # 多少多めに取って後で整形
            .all()
        )
        # 逆順（古い→新しい）
        q = list(reversed(q))

        short: List[HistoryMessage] = []
        user_cnt = 0
        asst_cnt = 0
        for row in q:
            role = row.role
            if role == "user":
                user_cnt += 1
            elif role == "assistant":
                asst_cnt += 1
            # SYSTEM_TRIGGERは role='system' で保存
            if role not in ("user", "assistant", "system"):
                continue

            short.append(
                HistoryMessage(
                    role=role, content=row.content or "", meta=row.metadata or {}
                )
            )
            # user/assistant が5往復（=10件）に達したら以降の user/assistant は打ち切り
            if user_cnt >= limit_rounds and asst_cnt >= limit_rounds:
                # ただし system は通す
                pass

        # 最新のユーザー入力（あれば）
        latest_user_message = None
        for row in reversed(q):
            if row.role == "user":
                latest_user_message = row.content
                break

        # 言語はセッション or ユーザー設定を参照（なければ "ja"）
        user_lang = session.user_lang or "ja"

        return AgentState(
            session_id=session_id,
            user_lang=user_lang,
            app_status=app_status,
            active_plan_id=active_plan_id,
            short_history=short,
            latest_user_message=latest_user_message,
        )


def save_message(session_id: str, role: str, content: str, meta: Optional[Dict[str, Any]] = None) -> None:
    """
    会話履歴に1件保存。SYSTEM_TRIGGERのときは role='system'＋専用メタ。
    """
    with get_db_session() as db:
        row = models.ConversationHistory(
            session_id=session_id,
            role=role,
            content=content,
            metadata=meta or {},
            created_at=_now_utc(),
        )
        db.add(row)
        db.commit()


def record_system_trigger(session_id: str, trigger_type: str, **kwargs: Any) -> None:
    """
    システムイベント（例：[SYSTEM_TRIGGER:PROXIMITY_GUIDE, spot_id:xxx]）を履歴保存。
    """
    meta = {"SYSTEM_TRIGGER": trigger_type}
    meta.update(kwargs or {})
    save_message(session_id=session_id, role="system", content=f"SYSTEM_TRIGGER:{trigger_type}", meta=meta)


def persist_session_status(session_id: str, app_status: Optional[str] = None, active_plan_id: Optional[int] = None):
    """
    セッションテーブルの current_status / active_plan_id を更新。
    """
    with get_db_session() as db:
        session = db.query(models.Session).filter(models.Session.session_id == session_id).one_or_none()
        if session is None:
            return
        if app_status is not None:
            session.current_status = app_status
        if active_plan_id is not None:
            session.active_plan_id = active_plan_id
        session.updated_at = _now_utc()
        db.add(session)
        db.commit()
