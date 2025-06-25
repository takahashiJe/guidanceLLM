# /backend/app/graph/build_graph.py (最終完成版)

from langgraph.graph import StateGraph, END
from shared.state import GraphState
from langchain_core.messages import AIMessage
from typing import List

from .nodes import (
    agent_node,
    tool_executor_node,
    classify_intent_node,
    propose_route_node,
    start_guidance_node,
    handle_rejection_node,
    handle_visit_plan_result_node,
    generate_simple_response_node,
    classify_confirmation_node,
)

def build_graph() -> StateGraph:
    """
    LangGraphのワークフローを構築し、コンパイル済みのグラフを返す。
    「ルーター」と「思考と行動のループ」を組み合わせた安定した設計。
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

    # --- 2. エントリーポイントと初期ルーターを設定 ---
    workflow.set_entry_point("classify_intent")

    def route_after_intent_classification(state: GraphState):
        if state.get("task_status") == "confirming_route":
            return "classify_confirmation"
        intent = state.get("intent")
        if intent == "greeting":
            return "simple_response"
        elif intent in ["route_request", "plan_visit_request", "general_question"]:
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

    # --- 3. 「思考と行動のループ」と、その後の分岐を定義 ---

    # (A) agentノードの実行後、ツールを呼ぶか、終了するかを判断
    def route_after_agent_thinks(state: GraphState):
        last_message = state['messages'][-1]
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            return "tools"
        return END

    workflow.add_conditional_edges("agent", route_after_agent_thinks)

    # (B) toolsノードの実行後、どの処理に進むかを判断
    def route_after_tool_execution(state: GraphState):
        # どのツールが呼ばれたか特定するために、最新のToolMessageの前のAIMessageを探す
        agent_decision_message = next((msg for msg in reversed(state['messages']) if isinstance(msg, AIMessage) and msg.tool_calls), None)
        
        if agent_decision_message:
            tool_name = agent_decision_message.tool_calls[0]['name']
            if tool_name == "calculate_route":
                return "propose_route" # ルート計算後は提案ノードへ
            elif tool_name == "check_and_plan_visit":
                return "handle_visit_plan" # 訪問計画の結果処理へ

        # 上記以外のツールが呼ばれた、または不明な場合は、ツールの実行結果を持って再度agentに戻る
        return "agent"

    workflow.add_conditional_edges(
        "tools",
        route_after_tool_execution,
        {
            "propose_route": "propose_route",
            "handle_visit_plan": "handle_visit_plan",
            "agent": "agent", # ★これが状態を維持する鍵
        }
    )

    # --- 4. 個別のフローを定義 ---
    workflow.add_edge("propose_route", "classify_confirmation")
    
    def route_after_user_confirms(state: GraphState):
        if state.get("intent") == "affirmative":
            return "start_guidance"
        else:
            return "handle_rejection"

    workflow.add_conditional_edges(
        "classify_confirmation",
        route_after_user_confirms,
        {
            "start_guidance": "start_guidance",
            "handle_rejection": "handle_rejection",
        }
    )

    # --- 5. 末端ノードからENDへの接続 ---
    workflow.add_edge("simple_response", END)
    workflow.add_edge("start_guidance", END)
    workflow.add_edge("handle_rejection", END)
    workflow.add_edge("handle_visit_plan", END)

    # --- 6. グラフをコンパイル ---
    return workflow.compile()

# アプリケーション起動時に一度だけグラフを構築
compiled_graph = build_graph()