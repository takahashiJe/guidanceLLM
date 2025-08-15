# backend/worker/app/services/orchestration/state.py
# ------------------------------------------------------------
# 役割:
#  - AgentState のロード/セーブ
#  - 短期記憶（直近5往復=最大10件）読み出し
#  - 応答確定後の ConversationEmbedding 永続化（user/assistant）
# ------------------------------------------------------------
from __future__ import annotations

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

from sqlalchemy.orm import Session

from shared.app.database import SessionLocal
from shared.app import models
from worker.app.services.embeddings import EmbeddingClient, save_conversation_embeddings


def _fetch_short_term_history(db: Session, session_id: str, limit_pairs: int = 5) -> List[Dict[str, Any]]:
    """
    直近の会話履歴（user/assistant/system 含む）を最大 10 件（5往復想定）取得。
    新しい順に取り、前後関係を保つために最後に時刻昇順で返す。
    """
    q = (
        db.query(models.ConversationHistory)
        .filter(models.ConversationHistory.session_id == session_id)
        .order_by(models.ConversationHistory.created_at.desc())
        .limit(limit_pairs * 2)
    )
    rows = list(reversed(q.all()))
    return [
        {
            "role": r.role,
            "content": r.content,
            "lang": r.lang,
            "created_at": r.created_at,
        }
        for r in rows
    ]


def load_agent_state(*, session_id: str) -> Dict[str, Any]:
    """
    セッションから現在のアプリ状態/短期記憶を構築。
    """
    with SessionLocal() as db:
        sess = db.query(models.Session).filter(models.Session.id == session_id).one_or_none()
        if sess is None:
            # セッションが無い場合は初期状態
            return {
                "session_id": session_id,
                "app_status": "browse",
                "active_plan_id": None,
                "short_term_history": [],
            }

        short_hist = _fetch_short_term_history(db, session_id=session_id, limit_pairs=5)

        return {
            "session_id": session_id,
            "app_status": sess.current_status,
            "active_plan_id": sess.active_plan_id,
            "short_term_history": short_hist,
        }


def save_agent_state(*, session_id: str, agent_state: Dict[str, Any]) -> None:
    """
    LangGraph 実行結果を DB に保存。
    - final_response を ConversationHistory に保存
    - SYSTEM_TRIGGER があれば保存
    - 直近ユーザー発話/アシスタント応答を ConversationEmbedding に保存
    """
    latest_user_message: Optional[str] = agent_state.get("latest_user_message")
    final_response: Optional[str] = agent_state.get("final_response")
    lang: Optional[str] = agent_state.get("lang") or "ja"
    app_status: Optional[str] = agent_state.get("app_status")
    active_plan_id: Optional[int] = agent_state.get("active_plan_id")

    emb_client = EmbeddingClient()

    with SessionLocal() as db:
        # セッション状態更新
        sess = db.query(models.Session).filter(models.Session.id == session_id).one_or_none()
        if sess is None:
            sess = models.Session(id=session_id)
            db.add(sess)
        if app_status:
            sess.current_status = app_status
        if active_plan_id is not None:
            sess.active_plan_id = active_plan_id

        # 1) ユーザー発話を履歴に保存（あれば）
        if latest_user_message:
            db.add(
                models.ConversationHistory(
                    session_id=session_id,
                    role="user",
                    content=latest_user_message,
                    lang=lang,
                )
            )

        # 2) システムトリガ（オプション）も履歴に保存（例: [SYSTEM_TRIGGER:...])
        sys_trig = agent_state.get("system_trigger_message")
        if isinstance(sys_trig, str) and sys_trig.strip():
            db.add(
                models.ConversationHistory(
                    session_id=session_id,
                    role="system",
                    content=sys_trig.strip(),
                    lang=lang,
                )
            )

        # 3) アシスタント最終応答を履歴保存
        if final_response:
            db.add(
                models.ConversationHistory(
                    session_id=session_id,
                    role="assistant",
                    content=final_response,
                    lang=lang,
                )
            )

        db.commit()

        # ===== 埋め込み保存 =====
        # 直近の user / assistant をそれぞれ埋め込み化
        entries: List[Tuple[str, str, Optional[str], str, List[float], str]] = []

        texts_to_embed: List[Tuple[str, str]] = []  # (speaker, text)
        if latest_user_message and latest_user_message.strip():
            texts_to_embed.append(("user", latest_user_message.strip()))
        if final_response and final_response.strip():
            texts_to_embed.append(("assistant", final_response.strip()))

        if texts_to_embed:
            # まとめて埋め込み（Ollama は1件ずつなので内部でループ）
            vectors = []
            for _, t in texts_to_embed:
                vectors.append(emb_client.embed_text(t))

            for (speaker, t), vec in zip(texts_to_embed, vectors):
                entries.append((session_id, speaker, lang, t, vec, emb_client.model))

            save_conversation_embeddings(db, entries)
