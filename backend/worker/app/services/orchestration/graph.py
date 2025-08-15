# -*- coding: utf-8 -*-
"""
graph.py
- LangGraph 配線（router → nodes）
- worker/app/tasks.py から呼び出されるエントリ runnable を公開
"""

from __future__ import annotations
from typing import Any, Dict

from langgraph.graph import StateGraph, END

from .state import AgentState, load_state
from .router import route_next
from .nodes.information_nodes import (
    information_entry,
    gather_nudge_and_pick_best,
    compose_nudge_response,
)
from .nodes.itinerary_nodes import (
    upsert_plan,
    calc_preview_route_and_summarize,
)
from .nodes.shared_nodes import chitchat_node, error_node


def _information_flow(state: AgentState) -> AgentState:
    """
    情報提供フロー：候補抽出 → ナッジ材料収集・最適日決定 → 提案文生成
    """
    try:
        state = information_entry(state)
        state = gather_nudge_and_pick_best(state)
        state = compose_nudge_response(state)
    except Exception as e:
        state = error_node(state, f"情報提供フローでエラー: {e}")
    return state


def _planning_flow(state: AgentState) -> AgentState:
    """
    計画フロー：CRUD → 暫定ルート計算 → LLM要約
    """
    try:
        state = upsert_plan(state)
        state = calc_preview_route_and_summarize(state)
    except Exception as e:
        state = error_node(state, f"計画フローでエラー: {e}")
    return state


def build_graph():
    """
    StateGraph を構築し、Runnable にコンパイルして返す。
    - 入口で router により information/planning/chitchat/__END__ に分岐
    - 各フローは単発実行（1ターン処理）。結果は state.final_response に格納。
    """
    sg = StateGraph(AgentState)

    # --- ノード定義 ---
    sg.add_node("information_flow", _information_flow)
    sg.add_node("planning_flow", _planning_flow)
    sg.add_node("chitchat", chitchat_node)

    # Entry: router ノード
    def _router(state: AgentState) -> AgentState:
        route = route_next(state)
        state.route = route
        return state

    sg.add_node("router", _router)

    # --- 条件分岐（router → 次ノード） ---
    def cond(state: AgentState) -> str:
        # route_next が返したラベルをそのまま次ノードとして使う
        return state.route or "__END__"

    sg.add_conditional_edges(
        "router",
        cond,
        {
            "information_flow": "information_flow",
            "planning_flow": "planning_flow",
            "chitchat": "chitchat",
            "__END__": END,
        },
    )

    # --- 各フローの終端 ---
    sg.add_edge("information_flow", END)
    sg.add_edge("planning_flow", END)
    sg.add_edge("chitchat", END)

    # --- エントリポイント ---
    sg.set_entry_point("router")

    return sg.compile()


# ====== 外部公開ランナブル（tasks.py から呼び出し） ======
graph_app = build_graph()


def run_orchestration(session_id: str) -> Dict[str, Any]:
    """
    Celery タスクから呼び出される実行関数。
      1) DBからセッション状態を復元（直近5往復＋SYSTEM_TRIGGER を短期記憶として含む）
      2) LangGraph を 1ターン実行
      3) 最終応答やプレビューGeoJSON等を返却
    """
    state = load_state(session_id)
    result_state: AgentState = graph_app.invoke(state)
    return {
        "session_id": result_state.session_id,
        "final_response": result_state.final_response,
        "app_status": result_state.app_status,
        "active_plan_id": result_state.active_plan_id,
        "bag": result_state.bag,  # 例: {"preview_geojson": ..., "nudge_materials": ...}
    }
