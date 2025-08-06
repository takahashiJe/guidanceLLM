# worker/app/services/orchestration/nodes/information_nodes.py

from shared.app.schemas import AgentState
from shared.app.database import session_scope
from worker.app.services.information.information_service import InformationService

# 専門サービスのインスタンス化
info_service = InformationService()

def search_spot_node(state: AgentState) -> AgentState:
    """
    InformationServiceを呼び出して、スポットを名前で検索するノード。
    """
    print("Executing search_spot_node...")
    user_query = state["userInput"]
    language = state["language"]
    
    with session_scope() as db:
        # 専門家（InformationService）に処理を依頼
        found_spots = info_service.find_spots_by_name(db, name=user_query, language=language)
    
    state["intermediateData"]["found_spots"] = found_spots
    print(f"Found {len(found_spots)} spots.")
    return state

def generate_spot_response_node(state: AgentState) -> AgentState:
    """
    検索結果を元に応答を生成するノード（スタブ実装）。
    """
    print("Executing generate_spot_response_node...")
    found_spots = state["intermediateData"].get("found_spots", [])
    
    if not found_spots:
        state["finalResponse"] = "申し訳ありません、その名前のスポットは見つかりませんでした。"
        return state

    # TODO: LLMInferenceServiceを呼び出し、検索結果を元に自然な応答文を生成する
    spot_names = [spot.official_name_ja for spot in found_spots]
    response_text = f"以下のスポットが見つかりました：{', '.join(spot_names)}"
    
    state["finalResponse"] = response_text
    return state