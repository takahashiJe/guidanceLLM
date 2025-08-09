# worker/app/services/orchestration/router.py

from shared.app.schemas import AgentState, Intent
from worker.app.services.llm.llm_service import LLMInferenceService

# LLMサービスのインスタンスを生成
llm_service = LLMInferenceService()

def route_conversation(state: AgentState) -> str:
    """
    LLMを使ってユーザーの意図を分類し、次に遷移すべきノード名を返す。
    """
    print("Routing conversation with LLM...")
    
    # 1. LLMサービスに意図分類を依頼
    #    ユーザーの最新の入力、会話履歴、現在のアプリ状態を全て考慮に入れる
    intent_result = llm_service.classify_intent(
        user_input=state["userInput"],
        history=state["chatHistory"],
        app_status=state["appStatus"],
        language=state["language"]
    )

    # 意図の分類に失敗した場合はエラーノードへ
    if not intent_result or not intent_result.get("intent"):
        print("Intent classification failed.")
        return "error_node"

    intent = intent_result.get("intent")
    print(f"LLM classified intent as: {intent}")

    # 2. アプリの状態（appStatus）に応じたルーティング
    
    # 計画モードの場合
    if state["appStatus"] == "planning":
        if intent == Intent.PLAN_EDIT_REQUEST:
            return "execute_plan_edit_node" # 計画編集を実行するノードへ
        if intent == Intent.PLAN_CONFIRMATION:
            return "confirm_plan_node" # 計画を確定するノードへ
        if intent == Intent.PLAN_CANCEL:
            return "cancel_plan_node" # 計画を中止するノードへ
        # 上記以外は、計画の現状を要約して確認を促す
        return "summarize_plan_node"

    # ブラウズモード（通常時）の場合
    if intent == Intent.PLAN_CREATION_REQUEST:
        return "create_plan_node"
    if intent in [Intent.SPECIFIC_SPOT_QUESTION, Intent.GENERAL_TOURIST_SPOT_QUESTION, Intent.CATEGORY_SPOT_QUESTION]:
        # 「育成型ナッジ」フローの開始点となるノードへ
        return "find_candidate_spots_node"
    if intent == Intent.CHITCHAT:
        return "chitchat_node"
    
    # いずれにも該当しない、予期せぬ意図の場合はエラーとして扱う
    print(f"Unknown intent '{intent}' for appStatus '{state['appStatus']}'.")
    return "error_node"