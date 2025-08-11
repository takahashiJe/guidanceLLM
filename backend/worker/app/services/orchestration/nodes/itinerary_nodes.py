# worker/app/services/orchestration/nodes/itinerary_nodes.py

from typing import Dict, Any
import logging

from backend.shared.app.schemas import AgentState
from backend.shared.app.database import session_scope
from backend.worker.app.services.itinerary.itinerary_service import ItineraryService
from backend.worker.app.services.llm.llm_service import LLMInferenceService

logger = logging.getLogger(__name__)
itinerary_service = ItineraryService()
llm_service = LLMInferenceService()

def create_plan_node(state: AgentState) -> Dict[str, Any]:
    """新しい計画を作成し、状態を更新する。"""
    logger.info(f"Executing create_plan_node for user {state['userId']}...")
    try:
        with session_scope() as db:
            new_plan = itinerary_service.create_new_plan(db, user_id=int(state["userId"]))
        
        return {
            "activePlanId": new_plan.plan_id,
            "appStatus": "planning"
        }
    except Exception as e:
        logger.error(f"Failed to create new plan: {e}", exc_info=True)
        return {"finalResponse": "申し訳ありません、計画の作成に失敗しました。"}


def extract_plan_edit_node(state: AgentState) -> Dict[str, Any]:
    """[本実装] LLMを使い、ユーザーの指示から計画編集のパラメータを抽出する。"""
    logger.info("Executing extract_plan_edit_node...")
    try:
        with session_scope() as db:
            plan = itinerary_service.get_plan_details(db, plan_id=state["activePlanId"])
        
        if not plan:
            return {"finalResponse": "編集対象の計画が見つかりません。"}

        edit_params = llm_service.extract_plan_edit_parameters(
            user_input=state["userInput"],
            current_stops=plan.stops,
            language=state["language"]
        )
        
        return {"intermediateData": {"edit_params": edit_params}}
    except Exception as e:
        logger.error(f"Failed to extract plan edit parameters: {e}", exc_info=True)
        return {"intermediateData": {"edit_params": None}}


def check_plan_edit_extraction(state: AgentState) -> Literal["success", "failure"]:
    """[本実装] 計画編集パラメータが正しく抽出できたか判定する。"""
    logger.info("Executing check_plan_edit_extraction...")
    edit_params = state.get("intermediateData", {}).get("edit_params")
    if edit_params and edit_params.get("action"):
        logger.info(f"Extraction successful: {edit_params}")
        return "success"
    else:
        logger.warning("Extraction failed. Routing back to summarize.")
        return "failure"


def execute_plan_edit_node(state: AgentState) -> Dict[str, Any]:
    """[本実装] 抽出されたパラメータに基づき、ItineraryServiceを呼び出して計画を編集する。"""
    logger.info("Executing execute_plan_edit_node...")
    edit_params = state["intermediateData"].get("edit_params")
    plan_id = state["activePlanId"]

    try:
        with session_scope() as db:
            action = edit_params["action"]
            if action == "add":
                itinerary_service.add_spot_to_plan(db, plan_id=plan_id, spot_name=edit_params["spot_name"], position_info=edit_params)
            elif action == "remove":
                itinerary_service.remove_spot_from_plan(db, plan_id=plan_id, spot_name=edit_params["spot_name"])
            # TODO: 順序変更(reorder)のロジックも追加
        
        return {} # 成功時は何も返さず、次のsummarizeノードに処理を任せる
    except Exception as e:
        logger.error(f"Failed to execute plan edit: {e}", exc_info=True)
        return {"finalResponse": "申し訳ありません、計画の編集に失敗しました。"}


def summarize_plan_node(state: AgentState) -> Dict[str, Any]:
    """現在の計画詳細を取得し、LLMで自然な要約文を生成する。"""
    logger.info(f"Executing summarize_plan_node for plan {state.get('activePlanId')}...")
    plan_id = state.get("activePlanId")
    if not plan_id:
        return {"finalResponse": "エラー：対象の計画が見つかりません。"}

    try:
        with session_scope() as db:
            plan = itinerary_service.get_plan_details(db, plan_id=plan_id)
        
        if not plan or not plan.stops:
            return {"finalResponse": "計画にはまだ何も登録されていません。どこか追加しますか？"}

        # ルート計算サービスを呼び出して暫定ルートを取得
        # route_geojson = routing_service.calculate_full_itinerary_route(...)
        
        summary_text = llm_service.generate_plan_summary(
            stops=plan.stops,
            language=state["language"]
        )
        
        return {
            "finalResponse": summary_text or "計画の要約を作成できませんでした。",
            # "intermediateData": {"route_geojson": route_geojson} # 地図更新用
        }
    except Exception as e:
        logger.error(f"Failed to summarize plan: {e}", exc_info=True)
        return {"finalResponse": "計画の要約中にエラーが発生しました。"}
