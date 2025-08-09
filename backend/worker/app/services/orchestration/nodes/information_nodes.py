# worker/app/services/orchestration/nodes/information_nodes.py

from datetime import date, timedelta
from shared.app.schemas import AgentState
from shared.app.database import session_scope
from worker.app.services.information.information_service import InformationService
from worker.app.services.llm.llm_service import LLMInferenceService

# 専門サービスのインスタンス化
info_service = InformationService()
llm_service = LLMInferenceService()

def find_candidate_spots_node(state: AgentState) -> AgentState:
    """[ナッジフェーズ1] ユーザーの意図に合った候補スポットを検索する。"""
    print("Executing find_candidate_spots_node...")
    
    # router.pyでの意図分類結果を取得 (router側でstateに格納する想定)
    intent_result = state["intermediateData"].get("intent_result", {})
    intent = intent_result.get("intent")
    query = intent_result.get("extracted_category") or state["userInput"]

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
    
    state["intermediateData"]["candidate_spots"] = found_spots
    print(f"Found {len(found_spots)} candidate spots.")
    return state

def gather_nudge_data_node(state: AgentState) -> AgentState:
    """[ナッジフェーズ2-A] 候補スポットのナッジ情報を収集する。"""
    print("Executing gather_nudge_data_node...")
    candidate_spots = state["intermediateData"].get("candidate_spots", [])
    if not candidate_spots:
        return state

    # 調査期間を決定（例：直近の週末）
    # TODO: ユーザーの発言から期間を抽出する
    today = date.today()
    start_date = today + timedelta(days=(5 - today.weekday() + 7) % 7) # 次の土曜日
    end_date = start_date + timedelta(days=1) # 日曜日

    with session_scope() as db:
        nudge_data = info_service.find_best_day_and_gather_nudge_data(
            db=db,
            spots=candidate_spots,
            user_location=state["intermediateData"].get("user_location", {}), # 仮
            date_range={"start": start_date, "end": end_date}
        )
    
    state["intermediateData"]["nudge_data_map"] = nudge_data
    return state

def select_best_spot_node(state: AgentState) -> AgentState:
    """[ナッジフェーズ2-B] ナッジ情報に基づき、提案するべき最適なスポットを1つ選択する。"""
    print("Executing select_best_spot_node...")
    nudge_data_map = state["intermediateData"].get("nudge_data_map", {})
    if not nudge_data_map:
        state["intermediateData"]["best_spot_to_propose"] = None
        return state

    # TODO: ここで高度な選択ロジックを実装できる
    #      今回はシンプルに、辞書の最初の要素を選択する
    best_spot_id = list(nudge_data_map.keys())[0]
    best_spot_nudge_data = nudge_data_map[best_spot_id]

    with session_scope() as db:
        spot_details = info_service.get_spot_details(db, spot_id=best_spot_id)

    state["intermediateData"]["best_spot_to_propose"] = {
        "details": spot_details,
        "nudge_data": best_spot_nudge_data
    }
    return state

def generate_nudge_proposal_node(state: AgentState) -> AgentState:
    """[ナッジフェーズ2-C] LLMを使い、説得力のあるナッジ提案文を生成する。"""
    print("Executing generate_nudge_proposal_node...")
    best_spot_info = state["intermediateData"].get("best_spot_to_propose")

    if not best_spot_info or not best_spot_info["details"]:
        state["finalResponse"] = "申し訳ありません、おすすめのスポットが見つかりませんでした。"
        return state

    proposal_text = llm_service.generate_nudge_proposal(
        nudge_data=best_spot_info["nudge_data"],
        spot_details=best_spot_info["details"],
        language=state["language"]
    )

    state["finalResponse"] = proposal_text or "おすすめのスポット情報を作成できませんでした。"
    return state