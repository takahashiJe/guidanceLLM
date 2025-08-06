# worker/app/services/orchestration/router.py

from shared.app.schemas import AgentState

def route_conversation(state: AgentState) -> str:
    """
    ユーザーの入力と現在のアプリの状態に基づいて、次に遷移すべきノード名を返す。
    （現段階では、キーワードに基づくシンプルなロジックで実装）
    """
    print(f"Routing conversation... AppStatus: {state['appStatus']}")
    user_message = state["userInput"]

    # TODO: LLMInferenceServiceを呼び出して、より高度な意図分類を行う
    
    # 計画モードの場合のルーティング
    if state["appStatus"] == "planning":
        # ここに計画編集のロジック（追加、削除など）が入る
        return "summarize_plan_node"

    # ブラウズモードの場合のルーティング
    if "計画" in user_message or "予定" in user_message:
        return "create_plan_node"
    elif "について教えて" in user_message or "って何" in user_message:
        return "search_spot_node"
    
    # デフォルトは雑談
    return "chitchat_node"