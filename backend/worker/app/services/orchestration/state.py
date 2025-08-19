# -*- coding: utf-8 -*-
"""
Orchestration State 管理（ロード/セーブ + 短期記憶 + 長期埋め込み）
--------------------------------------------------------------------
責務:
  - LangGraph 実行前の AgentState ロード（セッション情報・短期記憶）
  - 実行後の AgentState セーブ（会話履歴の確定・アプリ状態の保存）
  - セーブ時にユーザー発話/最終応答の埋め込みを ConversationEmbedding へ保存
  - conversation_id / turn_id の採番規則を一箇所に集約

注意:
  - スキーマ差分に備え、存在する列のみ安全に更新/挿入する（列名の自動検出）
  - SYSTEM_TRIGGER の履歴も ConversationHistory に残す（role/system）
"""

from __future__ import annotations

import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Literal, Tuple

from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from shared.app.database import SessionLocal
from shared.app import models
from worker.app.services.embeddings import EmbeddingService

logger = logging.getLogger(__name__)

# ------------------------------------------------------------
# 内部ユーティリティ: モデル列の検査と安全な dict 生成
# ------------------------------------------------------------

def _model_columns(model) -> List[str]:
    """SQLAlchemy モデルのカラム名一覧を返す。"""
    return [c.name for c in model.__table__.columns]  # type: ignore[attr-defined]


def _safe_kwargs(model, **kwargs) -> Dict[str, Any]:
    """存在するカラムだけを抽出して返す。"""
    cols = set(_model_columns(model))
    return {k: v for k, v in kwargs.items() if k in cols}


def _get_session_pk_name() -> str:
    """Session モデルの PK に使う列名（id or session_id）を返す。"""
    cols = set(_model_columns(models.Session))
    if "id" in cols:
        return "id"
    if "session_id" in cols:
        return "session_id"
    # 想定外ケース
    return "id"


def _get_history_text_field_name() -> str:
    """ConversationHistory の本文列名を返す（text / content / message の順で優先）。"""
    cols = set(_model_columns(models.ConversationHistory))
    for name in ("text", "content", "message"):
        if name in cols:
            return name
    # 想定外ケース
    return "text"


def _get_history_role_field_name() -> str:
    """ConversationHistory の話者列名（role/speaker）を返す。"""
    cols = set(_model_columns(models.ConversationHistory))
    return "role" if "role" in cols else ("speaker" if "speaker" in cols else "role")


def _get_history_time_field_name() -> str:
    """ConversationHistory の時刻列名（created_at/ts）を返す。"""
    cols = set(_model_columns(models.ConversationHistory))
    if "created_at" in cols:
        return "created_at"
    if "ts" in cols:
        return "ts"
    return "created_at"


def _get_history_conv_id_field_name() -> Optional[str]:
    """ConversationHistory の conversation_id 列名（存在しなければ None）。"""
    cols = set(_model_columns(models.ConversationHistory))
    if "conversation_id" in cols:
        return "conversation_id"
    return None


def _get_history_turn_id_field_name() -> Optional[str]:
    """ConversationHistory の turn_id 列名（存在しなければ None）。"""
    cols = set(_model_columns(models.ConversationHistory))
    if "turn_id" in cols:
        return "turn_id"
    return None


# ------------------------------------------------------------
# 公開: 短期記憶の取得（直近 N メッセージ）
# ------------------------------------------------------------

def get_recent_history(session_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    指定セッションの直近 'limit' 件の会話履歴を、時系列（古→新）で返す。
    - 短期記憶素材として利用（例: 直近5往復=10メッセージ）
    """
    with SessionLocal() as db:
        text_field = _get_history_text_field_name()
        role_field = _get_history_role_field_name()
        time_field = _get_history_time_field_name()

        q = (
            select(models.ConversationHistory)
            .where(getattr(models.ConversationHistory, "session_id") == session_id)
            .order_by(desc(getattr(models.ConversationHistory, time_field)))
            .limit(limit)
        )
        rows = list(reversed(db.execute(q).scalars().all()))
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "role": getattr(r, role_field, None),
                    "text": getattr(r, text_field, None),
                    "ts": getattr(r, time_field, None).isoformat() if getattr(r, time_field, None) else None,
                }
            )
        return out


def _build_short_term(history: List[Dict[str, Any]], turns: int = 5) -> List[Dict[str, Any]]:
    """
    直近 'turns' 往復（user/assistant の 2 メッセージを 1 往復とみなす）を返す。
    SYSTEM_TRIGGER は短期記憶には基本含めない（必要に応じて改変可能）。
    """
    # 末尾から user/assistant の組を拾う簡易実装
    filtered = [h for h in history if h.get("role") in ("user", "assistant")]
    take = max(0, min(len(filtered), turns * 2))
    return filtered[-take:]


# ------------------------------------------------------------
# 公開: AgentState のロード
# ------------------------------------------------------------

def load_agent_state(session_id: str) -> AgentState: # <- 返り値の型アノテーションを AgentState に変更
    """
    DB の Session / ConversationHistory から AgentState を構築して返す。
    """
    with SessionLocal() as db:
        sess = _get_or_create_session(db, session_id)
        full_recent = get_recent_history(session_id, limit=10) # List[Dict] を取得

        # DictのリストをChatItemのリストに変換
        chat_history_items = [
            ChatItem(
                role=item.get("role"),
                content=item.get("text"), # DBの列名'text'を'content'にマッピング
                created_at=datetime.fromisoformat(item["ts"]) if item.get("ts") else None
            )
            for item in full_recent
        ]

        # 最終的にAgentStateクラスのインスタンスを生成して返す
        return AgentState(
            session_id=session_id,
            app_status=getattr(sess, "app_status", "idle"),
            active_plan_id=getattr(sess, "active_plan_id", None),
            lang=getattr(sess, "lang", "ja"),
            chat_history=chat_history_items, # 変換後のリストをセット
            # その他のフィールドはデフォルト値で初期化される
        )


def _get_or_create_session(db: Session, session_id: str):
    """Session レコードを取得 or 作成。PK 列名差（id/session_id）にも対応。"""
    pk = _get_session_pk_name()
    Sess = models.Session
    q = select(Sess).where(getattr(Sess, pk) == session_id)
    row = db.execute(q).scalar_one_or_none()
    if row:
        return row
    # ない場合は作成（最小限の項目だけセット）
    kwargs = _safe_kwargs(
        Sess,
        **{
            pk: session_id,
            "app_status": "idle",
            "active_plan_id": None,
            "lang": "ja",
            "created_at": datetime.utcnow(),
        }
    )
    row = Sess(**kwargs)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ------------------------------------------------------------
# 公開: AgentState のセーブ（履歴 & セッション & 埋め込み）
# ------------------------------------------------------------

def save_agent_state(session_id: str, agent_state: Dict[str, Any]) -> None:
    """
    LangGraph 実行後の AgentState を永続化する。
    - Session の app_status / active_plan_id / lang を更新
    - ConversationHistory に ユーザー発話 / システム最終応答 を 1 ターンとして追記
    - 追記後、それぞれのテキストを ConversationEmbedding に保存（長期記憶）
    - SYSTEM_TRIGGER の場合は role='system' として履歴に含める
    """
    latest_user_message: Optional[str] = agent_state.get("latest_user_message")
    final_response: Optional[str] = agent_state.get("final_response")
    lang: str = agent_state.get("lang", "ja")
    app_status: Optional[str] = agent_state.get("app_status")
    active_plan_id: Optional[int] = agent_state.get("active_plan_id")

    # 会話 ID（セッション単位で固定）
    conversation_id: str = agent_state.get("conversation_id") or session_id

    with SessionLocal() as db:
        # 1) セッション更新（存在しなければ作成）
        sess = _get_or_create_session(db, session_id)
        _update_session_row(db, sess, app_status=app_status, active_plan_id=active_plan_id, lang=lang)

        # 2) turn_id を採番
        next_turn_id = _next_turn_id(db, session_id)

        # 3) ConversationHistory へ追記
        added_rows: List[Tuple[str, int, datetime, str]] = []  # (role, row_id, ts, text)

        # ユーザー発話 or SYSTEM_TRIGGER を記録
        if latest_user_message and latest_user_message.strip():
            user_role = "system" if latest_user_message.strip().startswith("[SYSTEM_TRIGGER") else "user"
            user_text = latest_user_message.strip()
            r_id, r_ts = _append_history(
                db=db,
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=next_turn_id,
                role=user_role,
                lang=lang,
                text=user_text,
            )
            added_rows.append((user_role, r_id, r_ts, user_text))

        # 最終応答を記録
        if final_response and final_response.strip():
            r_id, r_ts = _append_history(
                db=db,
                session_id=session_id,
                conversation_id=conversation_id,
                turn_id=next_turn_id,
                role="assistant",
                lang=lang,
                text=final_response.strip(),
            )
            added_rows.append(("assistant", r_id, r_ts, final_response.strip()))

        db.commit()  # 履歴コミット

        # 4) 長期記憶（埋め込み）を一括保存
        _save_embeddings_batch(
            rows=added_rows,
            session_id=session_id,
            conversation_id=conversation_id,
            lang=lang,
        )


def _update_session_row(
    db: Session,
    sess: Any,
    *,
    app_status: Optional[str],
    active_plan_id: Optional[int],
    lang: Optional[str],
) -> None:
    """Session 行を安全に更新。存在する列のみ書き込む。"""
    Sess = models.Session
    cols = set(_model_columns(Sess))
    dirty = False

    if app_status is not None and "app_status" in cols and getattr(sess, "app_status", None) != app_status:
        setattr(sess, "app_status", app_status)
        dirty = True

    if "active_plan_id" in cols and getattr(sess, "active_plan_id", None) != active_plan_id:
        setattr(sess, "active_plan_id", active_plan_id)
        dirty = True

    if lang is not None and "lang" in cols and getattr(sess, "lang", None) != lang:
        setattr(sess, "lang", lang)
        dirty = True

    if dirty:
        db.add(sess)
        db.commit()
        db.refresh(sess)


def _next_turn_id(db: Session, session_id: str) -> int:
    """
    次の turn_id を返す。
    - ConversationHistory に turn_id 列がある場合は MAX+1
    - なければ 1 を返し、以降も固定（列が無い環境では turn_id は未使用）
    """
    turn_col = _get_history_turn_id_field_name()
    if not turn_col:
        return 1

    CH = models.ConversationHistory
    q = (
        select(func.max(getattr(CH, turn_col)))
        .where(getattr(CH, "session_id") == session_id)
    )
    max_turn = db.execute(q).scalar()
    return int(max_turn or 0) + 1


def _append_history(
    *,
    db: Session,
    session_id: str,
    conversation_id: Optional[str],
    turn_id: Optional[int],
    role: str,
    lang: str,
    text: str,
) -> Tuple[int, datetime]:
    """
    ConversationHistory に 1 行挿入し、(row_id, ts) を返す。
    - 列名差に対応（text/content/message, role/speaker, created_at/ts, conversation_id/turn_id は存在時のみ）
    """
    CH = models.ConversationHistory

    text_field = _get_history_text_field_name()
    role_field = _get_history_role_field_name()
    time_field = _get_history_time_field_name()
    conv_field = _get_history_conv_id_field_name()
    turn_field = _get_history_turn_id_field_name()

    now = datetime.utcnow()

    kwargs = {
        "session_id": session_id,
        role_field: role,
        text_field: text,
        "lang": lang,
        time_field: now,
    }
    if conv_field and conversation_id:
        kwargs[conv_field] = conversation_id
    if turn_field and turn_id is not None:
        kwargs[turn_field] = turn_id

    row = CH(**_safe_kwargs(CH, **kwargs))
    db.add(row)
    db.flush()  # id 採番
    row_id: int = int(getattr(row, "id"))
    ts: datetime = getattr(row, time_field, now)
    return row_id, ts


def _save_embeddings_batch(
    *,
    rows: List[Tuple[str, int, datetime, str]],
    session_id: str,
    conversation_id: str,
    lang: str,
) -> None:
    """
    追加された履歴行に対して、埋め込み保存をまとめて実行。
    rows: List[(role, row_id, ts, text)]
    """
    if not rows:
        return
    svc = EmbeddingService()
    with SessionLocal() as db:
        for role, row_id, ts, text in rows:
            try:
                svc.upsert_message(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    speaker=("system" if role == "system" else role),
                    lang=lang,
                    text=text,
                    ts=ts,
                    db=db,
                )
            except Exception as e:
                # 埋め込み失敗はログのみ（本体の会話継続を阻害しない）
                logger.warning(f"embedding upsert failed: session={session_id} role={role} id={row_id} err={e}")
        db.commit()

Role = Literal["user", "assistant", "system"]
Mode = Literal["text", "voice"]
AppStatus = Literal["idle","information","planning","navigating","error"]
Intent = Literal["general_question","specific_question","plan_creation_request","plan_edit_request","chitchat","navigation_event","end"]

@dataclass
class ChatItem:
    role: Role
    content: str
    lang: Optional[str] = None
    created_at: Optional[datetime] = None

@dataclass
class SpotLite:
    id: int
    official_name: str
    lat: float
    lon: float
    tags: List[str] = field(default_factory=list)
    spot_type: Optional[str] = None

@dataclass
class AgentState:
    # A: セッション核
    session_id: str
    lang: str = "ja"
    app_status: AppStatus = "idle"
    active_plan_id: Optional[int] = None
    final_response: Optional[str] = None

    # B: 短期記憶
    chat_history: List[ChatItem] = field(default_factory=list)

    # C: 実行一回分入力
    latest_user_message: Optional[str] = None
    input_mode: Mode = "text"
    system_trigger: Optional[str] = None

    # D: NLU
    intent: Optional[Intent] = None
    plan_edit_params: Optional[Dict[str, Any]] = None
    intent_query: Optional[str] = None  # Eに寄せるならここでもOK

    # E: 情報フロー
    candidate_spots: List[SpotLite] = field(default_factory=list)
    nudge_materials: Dict[str, Any] = field(default_factory=dict)  # spot_id -> dict
    long_term_retrievals: List[Dict[str, Any]] = field(default_factory=list)

    # F: 計画フロー
    plan_ops: Optional[Dict[str, Any]] = None
    provisional_route: Optional[Dict[str, Any]] = None
    plan_summary_text: Optional[str] = None

    # G: ナビ
    is_navigating: bool = False  # ナビゲーションモードが有効かどうかのフラグ
    navigation_events: List[Dict[str, Any]] = field(default_factory=list)
    guide_generation_done: bool = False
    pre_generated_guides: Optional[List[dict]] = None # 事前生成されたガイドテキストのリスト

    # H: エラー/診断
    error: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)

# ==============================================================================================
# === ここから追記 =============================================================================
# ==============================================================================================

def save_message(
    state: AgentState,
    role: Literal["user", "assistant", "system"],
    content: str,
    meta: Optional[Dict[str, Any]] = None
) -> None:
    """
    AgentStateのchat_history（短期記憶）に新しいメッセージを追加するヘルパー関数。

    この関数は、`shared_nodes.py` などのLangGraphノード実装から呼び出されることを想定しています。
    状態（state）オブジェクトを直接変更することで、対話履歴を管理するロジックをこのファイルに集約し、
    コードの再利用性と保守性を高めます。

    Args:
        state (AgentState): 現在の対話状態を保持するオブジェクト。このオブジェクトの `chat_history` が更新されます。
        role (Literal["user", "assistant", "system"]): メッセージの送信者（役割）。
        content (str): メッセージの本文。
        meta (Optional[Dict[str, Any]], optional): ログや後続処理で使用するための追加情報。デフォルトはNone。
    """
    # 既存の AgentState が持つ ChatItem データクラスのインスタンスを作成します。
    new_message = ChatItem(
        role=role,
        content=content,
        # metaがNoneの場合は空の辞書をデフォルト値として設定します。
        meta=meta or {}
    )
    
    # 引数で受け取った state オブジェクトの chat_history リストに、新しいメッセージを追加します。
    # これにより、この関数を呼び出したノードの後続処理で、更新された対話履歴を参照できます。
    state.chat_history.append(new_message)