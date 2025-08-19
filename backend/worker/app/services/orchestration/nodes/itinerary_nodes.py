# /app/backend/worker/app/services/orchestration/nodes/itinerary_nodes.py

from datetime import date
from typing import Dict, Any, List

from langchain_core.messages import ToolMessage

from shared.app.database import SessionLocal
from worker.app.services.itinerary import itinerary_service
from worker.app.services.orchestration.state import AgentState


def _get_tool_call_param(tool_call: Dict[str, Any], param_name: str, default: Any = None) -> Any:
    """ツールコールの引数を安全に取得するヘルパー"""
    return tool_call.get("args", {}).get(param_name, default)


def create_plan_node(state: AgentState) -> Dict[str, Any]:
    """
    LLMからのツールコールに基づき、新しい周遊計画を作成するノード。
    """
    # 1. AgentStateから引数を抽出
    messages = state["messages"]
    tool_call = messages[-1].tool_calls[0] # 最新のツールコールを取得
    
    user_id = state["user_id"]
    session_id = state["session_id"]
    title = _get_tool_call_param(tool_call, "title", "新しい周遊計画")
    start_date_str = _get_tool_call_param(tool_call, "start_date")
    start_date = date.fromisoformat(start_date_str) if start_date_str else date.today()

    # 2. Serviceを呼び出す
    with SessionLocal() as db:
        plan_summary = itinerary_service.create_plan_for_user(
            db, user_id=user_id, session_id=session_id, title=title, start_date=start_date
        )

    # 3. AgentStateを更新
    tool_message = ToolMessage(
        content=f"新しい周遊計画を作成しました。計画IDは {plan_summary['plan_id']} です。",
        tool_call_id=tool_call["id"],
    )
    return {"plan": plan_summary, "messages": [tool_message]}


def add_spot_node(state: AgentState) -> Dict[str, Any]:
    """
    既存の計画にスポットを追加するノード。
    """
    # 1. AgentStateから引数を抽出
    plan_id = state.get("plan", {}).get("plan_id")
    if not plan_id:
        raise ValueError("計画がアクティブではありません。")

    messages = state["messages"]
    tool_call = messages[-1].tool_calls[0]
    spot_id = _get_tool_call_param(tool_call, "spot_id")
    position = _get_tool_call_param(tool_call, "position")

    if not spot_id:
         raise ValueError("spot_idが指定されていません。")

    # 2. Serviceを呼び出す
    with SessionLocal() as db:
        updated_plan_summary = itinerary_service.add_spot_to_user_plan(
            db, plan_id=plan_id, spot_id=spot_id, position=position
        )

    # 3. AgentStateを更新
    tool_message = ToolMessage(
        content=f"計画にスポット(ID: {spot_id})を追加しました。",
        tool_call_id=tool_call["id"],
    )
    return {"plan": updated_plan_summary, "messages": [tool_message]}


def remove_spot_node(state: AgentState) -> Dict[str, Any]:
    """
    既存の計画からスポットを削除するノード。
    """
    # 1. AgentStateから引数を抽出
    plan_id = state.get("plan", {}).get("plan_id")
    if not plan_id:
        raise ValueError("計画がアクティブではありません。")

    messages = state["messages"]
    tool_call = messages[-1].tool_calls[0]
    spot_id = _get_tool_call_param(tool_call, "spot_id")
    
    if not spot_id:
         raise ValueError("spot_idが指定されていません。")

    # 2. Serviceを呼び出す
    with SessionLocal() as db:
        updated_plan_summary = itinerary_service.remove_spot_from_user_plan(
            db, plan_id=plan_id, spot_id=spot_id
        )

    # 3. AgentStateを更新
    tool_message = ToolMessage(
        content=f"計画からスポット(ID: {spot_id})を削除しました。",
        tool_call_id=tool_call["id"],
    )
    return {"plan": updated_plan_summary, "messages": [tool_message]}


def reorder_stops_node(state: AgentState) -> Dict[str, Any]:
    """
    計画の訪問順を並べ替えるノード。
    """
    # 1. AgentStateから引数を抽出
    plan_id = state.get("plan", {}).get("plan_id")
    if not plan_id:
        raise ValueError("計画がアクティブではありません。")

    messages = state["messages"]
    tool_call = messages[-1].tool_calls[0]
    spot_ids_in_order = _get_tool_call_param(tool_call, "spot_ids_in_order")

    if not isinstance(spot_ids_in_order, list) or not spot_ids_in_order:
        raise ValueError("spot_ids_in_orderがリスト形式で正しく指定されていません。")

    # 2. Serviceを呼び出す
    with SessionLocal() as db:
        updated_plan_summary = itinerary_service.reorder_user_plan_stops(
            db, plan_id=plan_id, spot_ids_in_order=spot_ids_in_order
        )

    # 3. AgentStateを更新
    tool_message = ToolMessage(
        content=f"計画の訪問順を更新しました。",
        tool_call_id=tool_call["id"],
    )
    return {"plan": updated_plan_summary, "messages": [tool_message]}


def summarize_plan_node(state: AgentState) -> Dict[str, Any]:
    """
    計画の現在の状態を要約し、LLMに渡すための情報を生成するノード。
    """
    plan_id = state.get("plan", {}).get("plan_id")
    if not plan_id:
        raise ValueError("計画がアクティブではありません。")

    with SessionLocal() as db:
        plan_summary = itinerary_service.summarize_plan(db, plan_id=plan_id)
    
    # LLMに現在の計画を自然言語で説明させるためのコンテキストを生成
    stop_names = [s['name'] for s in plan_summary.get('stops', [])]
    summary_text = f"現在の計画には、{', '.join(stop_names)} が含まれています。"
    if plan_summary.get("total_duration_minutes", 0) > 0:
        summary_text += f" 全体の移動時間は約{plan_summary['total_duration_minutes']}分です。"

    # このノードはツールコールに応答するものではないため、ToolMessageは返さない
    # 代わりに、LLMが次の応答を生成するためのコンテキストをplan stateに含める
    plan_summary['llm_summary_context'] = summary_text
    
    return {"plan": plan_summary}