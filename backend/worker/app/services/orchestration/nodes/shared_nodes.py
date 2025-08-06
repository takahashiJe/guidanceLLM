# worker/app/services/orchestration/nodes/shared_nodes.py

from shared.app.schemas import AgentState

def chitchat_node(state: AgentState) -> AgentState:
    """
    雑談応答を生成するノード（スタブ実装）。
    """
    print("Executing chitchat_node...")
    # TODO: LLMInferenceServiceを呼び出して、自然な雑談応答を生成する
    user_message = state["userInput"]
    state["finalResponse"] = f"「{user_message}」についてですね。面白いお話です！"
    return state

def error_node(state: AgentState) -> AgentState:
    """
    エラー応答を生成するノード。
    """
    print("Executing error_node...")
    state["finalResponse"] = "申し訳ありません、うまく理解できませんでした。別の言葉で試していただけますか？"
    return state