# worker/app/services/orchestration/router.py

from backend.shared.app.schemas import AgentState, Intent
from backend.worker.app.services.llm.llm_service import LLMInferenceService
import logging

logger = logging.getLogger(__name__)
llm_service = LLMInferenceService()

def route_conversation(state: AgentState) -> str:
    """
    LLMを使ってユーザーの意図を分類し、次に遷移すべきノード名を返す。
    """
    logger.info(f"Routing conversation for session {state['sessionId']}...")
    
    try:
        intent_result = llm_service.classify_intent(
            user_input=state["userInput"],
            history=state["chatHistory"],
            app_status=state["appStatus"],
            language=state["language"]
        )

        if not intent_result or not intent_result.get("intent"):
            logger.warning("Intent classification failed. Routing to error node.")
            return "error"

        intent = intent_result.get("intent")
        logger.info(f"LLM classified intent as: {intent}")
        
        # 意図分類の結果を後続のノードで使えるようにstateに保存
        state["intermediateData"]["intent_result"] = intent_result

        # アプリの状態（appStatus）に応じたルーティング
        if state["appStatus"] == "planning":
            if intent == Intent.PLAN_EDIT_REQUEST:
                return "extract_plan_edit" # まず編集内容の抽出へ
            if intent == Intent.PLAN_CONFIRMATION:
                # TODO: 計画を確定し、ナビゲーションモードに移行するノードへ
                logger.info("Routing to (not implemented) confirm_plan_node")
                return "summarize_plan" # 仮
            if intent == Intent.PLAN_CANCEL:
                # TODO: 計画を中止し、ブラウズモードに戻るノードへ
                logger.info("Routing to (not implemented) cancel_plan_node")
                state["appStatus"] = "browsing"
                return "chitchat" # 仮
            # 上記以外は、計画の現状を要約して確認を促す
            return "summarize_plan"

        # ブラウズモード（通常時）の場合
        if intent == Intent.PLAN_CREATION_REQUEST:
            return "create_plan"
        if intent in [Intent.SPECIFIC_SPOT_QUESTION, Intent.GENERAL_TOURIST_SPOT_QUESTION, Intent.CATEGORY_SPOT_QUESTION]:
            return "find_candidate_spots"
        if intent == Intent.CHITCHAT:
            return "chitchat"
        
        logger.warning(f"Unknown intent '{intent}' for appStatus '{state['appStatus']}'.")
        return "error"

    except Exception as e:
        logger.error(f"An exception occurred during routing: {e}", exc_info=True)
        return "error"
