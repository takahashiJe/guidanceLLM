# /backend/shared/state.py

from typing import TypedDict, List, Optional, Literal, Annotated, Tuple
from langchain_core.messages import BaseMessage
import operator
from datetime import date
from shared.schemas import VisitPlanResponse, ActionPayload

class VisitPlanState(TypedDict):
    """訪問計画の内部状態"""
    spot_id: str
    spot_name: str
    visit_date: date

class GraphState(TypedDict):
    """LangGraph全体で共有される状態"""
    # --- 必須（外部から入力） ---
    messages: Annotated[List[BaseMessage], operator.add]
    task_status: Literal["idle", "confirming_route", "guiding", "planning_visit"]
    language: Literal["ja", "en", "zh", "other"] # ユーザーが選択した言語

    # --- 任意/生成される情報 ---
    user_id: Optional[str]
    current_location: Optional[Tuple[float, float]]

    visit_plan: Optional[VisitPlanState]

    intent: Optional[Literal["greeting", "general_question", "route_request", "plan_visit_request", "affirmative", "negative"]]
    tool_outputs: Optional[List[dict]]
    final_answer: Optional[str]
    action_payload: Optional[ActionPayload]

    # クエリ拡張用フィールド
    original_query: Optional[str] # ユーザーの元の質問
    expanded_queries: Optional[List[str]] # LLMによって拡張された質問のリスト
    
    # ツールからの出力だけでなく、RAGの結果もここに格納
    context_documents: Optional[List[dict]] # RAGで取得したドキュメント