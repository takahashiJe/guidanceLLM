# -*- coding: utf-8 -*-
"""
router.py
- ユーザー意図の分類（LLM推論サービス）→ LangGraphの遷移先を決定
"""

from __future__ import annotations
from typing import Literal

from worker.app.services.llm.llm_service import LLMInferenceService
from .state import AgentState


def route_next(state: AgentState) -> Literal["information_flow", "planning_flow", "chitchat", "__END__"]:
    """
    LLMにより意図分類し、LangGraphの遷移先ラベルを返す。
    app_statusも加味して、planning中は編集リクエストを優先などのルールを実装。
    """
    llm = LLMInferenceService()

    # 空入力などはEND
    if not state.latest_user_message:
        return "__END__"

    intent = llm.classify_intent(
        user_message=state.latest_user_message,
        app_status=state.app_status,
        history=[m.model_dump() for m in state.short_history],
        lang=state.user_lang,
    )

    intent_type = intent.intent  # e.g., "general_question", "specific_question", "plan_creation_request", "plan_edit_request", "chitchat"

    # アプリ状態と意図で分岐
    if intent_type in ("general_question", "specific_question"):
        return "information_flow"

    if intent_type in ("plan_creation_request", "plan_edit_request") or state.app_status == "planning":
        return "planning_flow"

    if intent_type == "chitchat":
        return "chitchat"

    return "__END__"
