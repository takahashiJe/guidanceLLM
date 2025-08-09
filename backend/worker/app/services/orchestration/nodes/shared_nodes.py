# worker/app/services/orchestration/nodes/shared_nodes.py

from shared.app.schemas import AgentState
from worker.app.services.llm.llm_service import LLMInferenceService

# LLMサービスのインスタンスを生成
llm_service = LLMInferenceService()

def chitchat_node(state: AgentState) -> AgentState:
    """LLMを使い、自然な雑談応答を生成するノード。"""
    print("Executing chitchat_node with LLM...")
    
    response_text = llm_service.generate_chitchat_response(
        history=state["chatHistory"],
        language=state["language"]
    )
    
    state["finalResponse"] = response_text or "ごめんなさい、うまくお返事できませんでした。"
    return state

def error_node(state: AgentState) -> AgentState:
    """LLMを使い、丁寧なエラー応答を生成するノード。"""
    print("Executing error_node with LLM...")

    response_text = llm_service.generate_error_message(
        language=state["language"]
    )
    
    state["finalResponse"] = response_text or "申し訳ありません、予期せぬエラーが発生しました。"
    return state