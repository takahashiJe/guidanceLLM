# worker/app/services/orchestration/nodes/information_nodes.py

from datetime import date, timedelta
from typing import Dict, Any, Literal
import logging

from backend.shared.app.schemas import AgentState
from backend.shared.app.database import session_scope
from backend.worker.app.services.information.information_service import InformationService
from backend.worker.app.services.llm.llm_service import LLMInferenceService

logger = logging.getLogger(__name__)
info_service = InformationService()
llm_service = LLMInferenceService()

def find_candidate_spots_node(state: AgentState) -> Dict[str, Any]:
    """[ナッジフェーズ1] ユーザーの意図に合った候補スポットを検索する。"""
    logger.info("Executing find_candidate_spots_node...")
    try:
        intent_result = state["intermediateData"].get("intent_result", {})
        intent = intent_result.get("intent")
        query = intent_result.get("extracted_entity") or state["userInput"]

        intent_map = {
            "specific_spot_question": "specific",
            "general_tourist_spot_question": "general_tourist",
            "category_spot_question": "category",
        }
        intent_type = intent_map.get(intent, "general_tourist")

        with session_scope() as db:
            found_spots = info_service.find_spots_by_intent(
                db, intent_type=intent_type, query=query, language=state["language"]
            )
        
        logger.info(f"Found {len(found_spots)} candidate spots.")
        return {"intermediateData": {"candidate_spots": found_spots}}
    except Exception as e:
        logger.error(f"Failed to find candidate spots: {e}", exc_info=True)
        return {"intermediateData": {"candidate_spots": []}}


def check_spot_found(state: AgentState) -> Literal["continue", "stop"]:
    """[本実装] 候補スポットが見つかったかどうかを判定する条件分岐用の関数。"""
    logger.info("Executing check_spot_found...")
    if state.get("intermediateData", {}).get("candidate_spots"):
        return "continue"
    else:
        return "stop"


def handle_no_spot_found_node(state: AgentState) -> Dict[str, Any]:
    """[本実装] 候補スポットが見つからなかった場合の応答を生成する。"""
    logger.info("Executing handle_no_spot_found_node...")
    # TODO: LLMを使って、より文脈に合わせた応答を生成することも可能
    return {"finalResponse": "申し訳ありません、ご要望に合うスポットが見つかりませんでした。別のキーワードでお探ししますか？"}


def gather_nudge_data_node(state: AgentState) -> Dict[str, Any]:
    """[ナッジフェーズ2-A] 候補スポットのナッジ情報を収集する。"""
    logger.info("Executing gather_nudge_data_node...")
    candidate_spots = state["intermediateData"].get("candidate_spots", [])
    if not candidate_spots:
        return {}

    try:
        # TODO: ユーザーの発言から期間を抽出する
        today = date.today()
        start_date = today + timedelta(days=(5 - today.weekday() + 7) % 7)
        end_date = start_date + timedelta(days=1)

        with session_scope() as db:
            nudge_data = info_service.find_best_day_and_gather_nudge_data(
                db=db,
                spots=candidate_spots,
                user_location={"latitude": 39.0, "longitude": 140.0}, # TODO: ユーザーの実際の場所を使う
                date_range={"start": start_date, "end": end_date}
            )
        
        return {"intermediateData": {"nudge_data_map": nudge_data}}
    except Exception as e:
        logger.error(f"Failed to gather nudge data: {e}", exc_info=True)
        return {"intermediateData": {"nudge_data_map": {}}}


def select_best_spot_node(state: AgentState) -> Dict[str, Any]:
    """[ナッジフェーズ2-B] 最適なスポットを1つ選択する。"""
    logger.info("Executing select_best_spot_node...")
    nudge_data_map = state["intermediateData"].get("nudge_data_map", {})
    if not nudge_data_map:
        return {"intermediateData": {"best_spot_to_propose": None}}

    # TODO: より高度な選択ロジックを実装
    best_spot_id = list(nudge_data_map.keys())[0]
    best_spot_nudge_data = nudge_data_map[best_spot_id]

    with session_scope() as db:
        spot_details = info_service.get_spot_details(db, spot_id=best_spot_id)

    return {"intermediateData": {"best_spot_to_propose": {
        "details": spot_details,
        "nudge_data": best_spot_nudge_data
    }}}

def generate_nudge_proposal_node(state: AgentState) -> Dict[str, Any]:
    """[ナッジフェーズ2-C] LLMを使い、説得力のあるナッジ提案文を生成する。"""
    logger.info("Executing generate_nudge_proposal_node...")
    best_spot_info = state["intermediateData"].get("best_spot_to_propose")

    if not best_spot_info or not best_spot_info.get("details"):
        return {"finalResponse": "申し訳ありません、おすすめのスポットが見つかりませんでした。"}

    try:
        proposal_text = llm_service.generate_nudge_proposal(
            nudge_data=best_spot_info["nudge_data"],
            spot_details=best_spot_info["details"],
            language=state["language"]
        )
        return {"finalResponse": proposal_text or "おすすめのスポット情報を作成できませんでした。"}
    except Exception as e:
        logger.error(f"Failed to generate nudge proposal: {e}", exc_info=True)
        return {"finalResponse": "おすすめ情報の作成中にエラーが発生しました。"}
