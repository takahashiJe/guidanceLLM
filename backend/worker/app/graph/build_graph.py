# /backend/app/graph/build_graph.py

from langgraph.graph import StateGraph, END
from ..shared/state import GraphState
from .nodes import (
    agent_node,
    tool_executor_node,
    classify_intent_node,
    classify_confirmation_node,
    propose_route_node,
    start_guidance_node,
    handle_rejection_node,
    handle_visit_plan_result_node,
    generate_simple_response_node,
)

def build_graph() -> StateGraph:
    """
    LangGraphのワークフローを構築し、コンパイル済みのグラフを返す。
    """
    workflow = StateGraph(GraphState)

    # --- 1. ノードをすべて登録 ---
    workflow.add_node("classify_intent", classify_intent_node)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_executor_node)
    workflow.add_node("propose_route", propose_route_node)
    workflow.add_node("classify_confirmation", classify_confirmation_node)
    workflow.add_node("start_guidance", start_guidance_node)
    workflow.add_node("handle_rejection", handle_rejection_node)
    workflow.add_node("handle_visit_plan", handle_visit_plan_result_node)
    workflow.add_node("simple_response", generate_simple_response_node)

    # --- 2. エントリーポイントを設定 ---
    workflow.set_entry_point("classify_intent")

    # --- 3. 条件分岐とエッジを定義 ---

    # Entry Point -> classify_intent
    def route_after_intent_classification(state: GraphState):
        """意図分類後のルーティング"""
        if state.get("task_status") == "confirming_route":
            return "classify_confirmation"
        
        intent = state.get("intent")
        if intent == "greeting":
            return "simple_response"
        elif intent in ["general_question", "route_request", "plan_visit_request"]:
            return "agent"
        else:
            return END

    workflow.add_conditional_edges(
        "classify_intent",
        route_after_intent_classification,
        {
            "classify_confirmation": "classify_confirmation",
            "agent": "agent",
            "simple_response": "simple_response",
            END: END
        }
    )

    # agent -> (tools or END)
    def route_after_agent(state: GraphState):
        """エージェントの思考後のルーティング"""
        if state['messages'][-1].tool_calls:
            return "tools"
        return END

    workflow.add_conditional_edges("agent", route_after_agent, {"tools": "tools", END: END})

    # tools -> (agent or propose_route or handle_visit_plan)
    def route_after_tool_execution(state: GraphState):
        """ツール実行後のルーティング"""
        last_tool_name = state["tool_outputs"][-1]["tool"]
        if last_tool_name == "calculate_route":
            return "propose_route" # ルート計算後は提案へ
        elif last_tool_name == "check_and_plan_visit":
            return "handle_visit_plan" # 訪問計画の結果処理へ
        else:
            return "agent" # 他のツール結果はエージェントに戻す

    workflow.add_conditional_edges(
        "tools",
        route_after_tool_execution,
        {
            "propose_route": "propose_route",
            "handle_visit_plan": "handle_visit_plan",
            "agent": "agent",
        }
    )

    # propose_route -> classify_confirmation
    workflow.add_edge("propose_route", "classify_confirmation")

    # classify_confirmation -> (start_guidance or handle_rejection)
    def route_after_confirmation(state: GraphState):
        """ルート提案へのユーザーの応答に基づくルーティング"""
        if state.get("intent") == "affirmative":
            return "start_guidance"
        else:
            return "handle_rejection"

    workflow.add_conditional_edges(
        "classify_confirmation",
        route_after_confirmation,
        {
            "start_guidance": "start_guidance",
            "handle_rejection": "handle_rejection",
        }
    )

    # --- 4. 末端ノードからENDへの接続 ---
    workflow.add_edge("simple_response", END)
    workflow.add_edge("start_guidance", END)
    workflow.add_edge("handle_rejection", END)
    workflow.add_edge("handle_visit_plan", END)

    # --- 5. グラフをコンパイル ---
    app = workflow.compile()
    return app

# アプリケーション起動時に一度だけグラフを構築
compiled_graph = build_graph()
