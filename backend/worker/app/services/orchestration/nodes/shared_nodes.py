# -*- coding: utf-8 -*-
"""
shared_nodes.py
- 共有ノード（雑談/エラー/ユーティリティ）
- LangGraph の複数フローで共通利用する小さな処理を集約
"""

from __future__ import annotations
from typing import Any

from worker.app.services.llm.llm_service import LLMInferenceService
from worker.app.services.orchestration.state import AgentState, save_message


def safe_set_final_response(state: AgentState, text: str) -> None:
    """
    最終応答を状態に反映し、会話履歴（assistant）にも保存するユーティリティ。
    - どのフローでも使える共通処理。
    """
    state.final_response = text
    save_message(state.session_id, "assistant", text, meta={"type": "generic"})


def chitchat_node(state: AgentState) -> AgentState:
    """
    雑談応答ノード：
      - 直近履歴（短期記憶）とユーザーの最新メッセージを LLM に渡して自然応答を生成。
    """
    llm = LLMInferenceService()
    msg = llm.generate_chitchat_response(
        lang=state.user_lang,
        history=[m.model_dump() for m in state.short_history],
        user_message=state.latest_user_message or "",
    )
    state.final_response = msg
    save_message(state.session_id, "assistant", msg, meta={"type": "chitchat"})
    return state


def error_node(state: AgentState, detail: str = "") -> AgentState:
    """
    エラー応答ノード：
      - 固いエラー文ではなく、次の一手を提案する優しいメッセージを LLM で生成。
    """
    llm = LLMInferenceService()
    msg = llm.generate_error_message(lang=state.user_lang, context=detail)
    state.final_response = msg
    save_message(state.session_id, "assistant", msg, meta={"type": "error"})
    return state
