# /backend/app/graph/build_graph.py

from langgraph.graph import StateGraph, END
from shared.state import GraphState
from langchain_core.messages import AIMessage, ToolMessage
from typing import List
import json

# ★★★ 1. RAG関連ノードをインポートリストに追加 ★★★
from worker.app.graph.nodes import (
    agent_node,
    tool_executor_node,
    classify_intent_node,
    propose_route_node,
    start_guidance_node,
    handle_rejection_node,
    handle_visit_plan_result_node,
    generate_simple_response_node,
    classify_confirmation_node,
    query_expansion_node,
    multi_rag_retrieval_node,
    rag_synthesis_node,
)

def build_graph() -> StateGraph:
    """
    LangGraphのワークフローを構築し、コンパイル済みのグラフを返す。
    「ルーター」と「思考と行動のループ」を組み合わせた安定した設計。
    """
    workflow = StateGraph(GraphState)

    # --- 1. ノードをすべて登録 ---
    # ★★★ 2. RAG関連ノードをワークフローに登録 ★★★
    workflow.add_node("classify_intent", classify_intent_node)
    workflow.add_node("query_expansion", query_expansion_node)  
    workflow.add_node("multi_rag_retrieval", multi_rag_retrieval_node) 
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_executor_node)
    workflow.add_node("propose_route", propose_route_node)
    workflow.add_node("classify_confirmation", classify_confirmation_node)
    workflow.add_node("start_guidance", start_guidance_node)
    workflow.add_node("handle_rejection", handle_rejection_node)
    workflow.add_node("handle_visit_plan", handle_visit_plan_result_node)
    workflow.add_node("simple_response", generate_simple_response_node)
    workflow.add_node("rag_synthesis", rag_synthesis_node)

    # --- 2. エントリーポイントと初期ルーターを設定 ---
    workflow.set_entry_point("classify_intent")

    # ★★★ 3. 意図分類後の交通整理（ルーティング）を修正 ★★★
    def route_after_intent_classification(state: GraphState):
        if state.get("task_status") == "confirming_route":
            return "classify_confirmation"
        intent = state.get("intent")
        if intent == "greeting":
            return "simple_response"
        elif intent == "general_question":
            # 「一般的な質問」の場合は、RAGプロセス（クエリ拡張）へ進む
            return "query_expansion"
        elif intent in ["route_request", "plan_visit_request"]:
            # ルートや計画の質問は、ツールを直接使うagentへ進む
            return "agent"
        else:
            return END

    workflow.add_conditional_edges(
        "classify_intent",
        route_after_intent_classification,
        {
            "classify_confirmation": "classify_confirmation",
            "query_expansion": "query_expansion",
            "agent": "agent",
            "simple_response": "simple_response",
            END: END
        }
    )
    
    # ★★★ 4. RAGプロセスの流れを定義 ★★★
    # クエリ拡張 → RAG検索 → エージェント、という流れを接続
    workflow.add_edge("query_expansion", "multi_rag_retrieval")
    workflow.add_edge("multi_rag_retrieval", "rag_synthesis")
    workflow.add_edge("rag_synthesis", END) 

    # --- 3. 「思考と行動のループ」と、その後の分岐を定義 ---
    def route_after_agent_thinks(state: GraphState):
        last_message = state['messages'][-1]
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            return "tools"
        return END

    workflow.add_conditional_edges("agent", route_after_agent_thinks)

    def route_after_tool_execution(state: GraphState):
        agent_decision_message = next((msg for msg in reversed(state['messages']) if isinstance(msg, AIMessage) and msg.tool_calls), None)
        last_tool_message = state['messages'][-1] if state['messages'] else None
        
        if agent_decision_message:
            tool_name = agent_decision_message.tool_calls[0]['name']
            if tool_name == "calculate_route":
                if isinstance(last_tool_message, ToolMessage):
                    try:
                        tool_output = json.loads(last_tool_message.content)
                        if tool_output.get("status") in ["error", "outside_area"]:
                            return "agent" 
                        else:
                            return "propose_route"
                    except json.JSONDecodeError:
                        return "agent"
            elif tool_name == "manage_visit_plan":
                return "handle_visit_plan"
        return "agent"

    workflow.add_conditional_edges(
        "tools",
        route_after_tool_execution,
        {
            "propose_route": "propose_route",
            "handle_visit_plan": "handle_visit_plan",
            "agent": "agent",
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