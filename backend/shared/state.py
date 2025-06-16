# /backend/shared/state.py

from typing import TypedDict, List, Optional, Literal, Annotated
from langchain_core.messages import BaseMessage
import operator

class ActionPayload(TypedDict, total=False):
    """フロントエンドへのアクション指示のスキーマ"""
    type: Literal["draw_route", "highlight_spot"]
    payload: dict

class GraphState(TypedDict):
    """LangGraph全体で共有される状態"""
    # --- 必須（外部から入力） ---
    messages: Annotated[List[BaseMessage], operator.add]
    task_status: Literal["idle", "confirming_route", "guiding", "planning_visit"]
    language: Literal["ja", "en", "zh", "other"] # ユーザーが選択した言語

    # --- 任意/生成される情報 ---
    user_id: Optional[str]
    intent: Optional[Literal["greeting", "general_question", "route_request", "plan_visit_request", "affirmative", "negative"]]
    tool_outputs: Optional[List[dict]]
    final_answer: Optional[str]
    action_payload: Optional[ActionPayload]