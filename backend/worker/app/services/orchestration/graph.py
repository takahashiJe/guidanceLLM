# /app/backend/worker/app/services/orchestration/graph.py

# -*- coding: utf-8 -*-
"""
graph.py
- LangGraph 配線（router → nodes）
- worker/app/tasks.py から呼び出されるエントリ runnable を公開
"""

from __future__ import annotations
from typing import Any, Dict

from langgraph.graph import StateGraph, END

# =================================================================
# === 変更点①: state.py の import を修正 ==========================
# =================================================================
# 既存コードで`run_orchestration`が定義されていたため、`load_agent_state`のみをimport
from .state import AgentState, load_agent_state
from .router import route_next
from .nodes.information_nodes import (
    information_entry,
    gather_nudge_and_pick_best,
    compose_nudge_response,
)
# =================================================================
# === 変更点②: import する関数名を修正 =============================
# =================================================================
from .nodes.itinerary_nodes import (
    upsert_plan_node,
    calc_preview_route_and_summarize_node,
)
from .nodes.shared_nodes import chitchat_node, error_node
from .nodes.navigation_nodes import start_navigation_node, end_navigation_node


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
        # =================================================================
        # === 変更点③: 呼び出す関数名を修正 ================================
        # =================================================================
        state = upsert_plan_node(state)
        state = calc_preview_route_and_summarize_node(state)
    except Exception as e:
        state = error_node(state, f"計画フローでエラー: {e}")
    return state


def build_graph() -> StateGraph:
    """
    LangGraph のノードとエッジを定義してグラフを構築する。
    """
    sg = StateGraph(AgentState)

    # --- ノード定義 ---
    sg.add_node("router", route_next)
    sg.add_node("information_flow", _information_flow)
    sg.add_node("planning_flow", _planning_flow)
    sg.add_node("chitchat", chitchat_node)
    sg.add_node("start_navigation", start_navigation_node)
    sg.add_node("end_navigation", end_navigation_node)

    # --- エッジ定義（Router → 次ノード） ---
    def cond(state: AgentState) -> str:
        # =================================================================
        # === 変更点④: state.route を state["route_name"] に修正 ===========
        # =================================================================
        # router.pyの実装に合わせ、`state.get("route_name")`で判定
        return state.get("route_name") or "__END__"

    sg.add_conditional_edges(
        "router",
        cond,
        {
            "information_flow": "information_flow",
            "planning_flow": "planning_flow",
            "chitchat": "chitchat",
            "navigation": "start_navigation",
            "end_navigation": "end_navigation",
            "__END__": END,
        },
    )

    # --- 各フローの終端 ---
    sg.add_edge("information_flow", END)
    sg.add_edge("planning_flow", END)
    sg.add_edge("chitchat", END)
    sg.add_edge("start_navigation", END)
    sg.add_edge("end_navigation", END)

    # --- エントリポイント ---
    sg.set_entry_point("router")

    return sg.compile()


# ====== 外部公開ランナブル（tasks.py から呼び出し） ======
graph_app = build_graph()