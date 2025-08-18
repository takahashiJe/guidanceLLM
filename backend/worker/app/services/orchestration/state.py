# worker/app/services/orchestration/state.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from sqlalchemy.orm import Session

from shared.app.database import SessionLocal
from shared.app import models, schemas

# 既存の import 期待に合わせて、EmbeddingClient / save_conversation_embeddings をここから参照できるようにする
from worker.app.services.embeddings import (
    EmbeddingClient,
    create_embedding_client,
    save_conversation_embeddings,
)


# ========================
# ドメイン状態
# ========================
@dataclass
class AgentState:
    """
    オーケストレーションで使う対話状態のコンテナ。
    既存の schemas.AgentState との互換を保つため、必要なフィールドは維持。
    """
    session_id: str
    lang: str = "ja"
    history: List[Dict[str, Any]] = field(default_factory=list)  # [{role, content, ...}]
    app_status: Optional[str] = None
    active_plan_id: Optional[str] = None
    # 他に必要なフィールドがあれば増やすが、既存用途を壊さない範囲に留める


# ========================
# 状態のロード / 反映
# ========================
def load_state(session_id: str) -> AgentState:
    """
    DB から会話履歴やセッション情報を読み込み、AgentState を構成する。
    既存の「復元処理」の目的を踏襲。
    """
    with SessionLocal() as db:  # type: Session
        sess = db.query(models.Session).filter(models.Session.session_id == session_id).first()
        if not sess:
            # セッションがない場合も壊さず返す（上位で生成済みの想定）
            return AgentState(session_id=session_id)

        # 会話履歴（role, content の一覧を作る）
        # 既存モデルに合わせて ConversationHistory を role/content に射影
        histories = (
            db.query(models.ConversationHistory)
            .filter(models.ConversationHistory.session_id == session_id)
            .order_by(models.ConversationHistory.created_at.asc())
            .all()
        )

        history_payload: List[Dict[str, Any]] = []
        for h in histories:
            role = "user" if h.is_user else "assistant"
            history_payload.append({"role": role, "content": h.content})

        state = AgentState(
            session_id=session_id,
            lang=sess.lang or "ja",
            history=history_payload,
            app_status=sess.app_status,
            active_plan_id=sess.active_plan_id,
        )
        return state


def apply_state_update(
    session_id: str,
    new_messages: List[Dict[str, Any]],
    app_status: Optional[str] = None,
    active_plan_id: Optional[str] = None,
    embed_client: Optional[EmbeddingClient] = None,
) -> None:
    """
    会話の新規メッセージを保存し、必要なら埋め込みも保存する。
    既存の「情報フロー確定時の副作用」を踏襲。
    """
    if not new_messages:
        return

    with SessionLocal() as db:  # type: Session
        # 履歴の永続化
        for m in new_messages:
            role = (m.get("role") or "user").lower()
            is_user = role in ("user", "human")
            content = str(m.get("content") or m.get("text") or "").strip()
            if not content:
                continue
            rec = models.ConversationHistory(
                session_id=session_id,
                is_user=is_user,
                content=content,
            )
            db.add(rec)

        # セッションのステータスの更新（必要なら）
        sess = (
            db.query(models.Session)
            .filter(models.Session.session_id == session_id)
            .with_for_update(nowait=False)
            .first()
        )
        if sess:
            if app_status is not None:
                sess.app_status = app_status
            if active_plan_id is not None:
                sess.active_plan_id = active_plan_id

        db.commit()

    # 埋め込み保存（上書きしない。追記）
    try:
        client = embed_client or create_embedding_client()
        save_conversation_embeddings(session_id=session_id, messages=new_messages, client=client)
    except Exception:
        # 埋め込み失敗は対話継続を阻害しない（既存の堅牢性の目的）
        # ただしログ基盤があるなら警告を出すのが望ましい
        pass
