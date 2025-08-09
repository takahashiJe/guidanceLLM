# worker/app/services/orchestration/nodes/itinerary_nodes.py

from shared.app.schemas import AgentState
from shared.app.database import session_scope
from worker.app.services.itinerary.itinerary_service import ItineraryService
from worker.app.services.llm.llm_service import LLMInferenceService

# 専門サービスのインスタンス化
itinerary_service = ItineraryService()
llm_service = LLMInferenceService()

def create_plan_node(state: AgentState) -> AgentState:
    """ItineraryServiceを呼び出して新しい計画を作成し、状態を更新する。"""
    print("Executing create_plan_node...")
    with session_scope() as db:
        new_plan = itinerary_service.create_new_plan(db, user_id=int(state["userId"]))
    
    state['activePlanId'] = new_plan.plan_id
    state['appStatus'] = 'planning'
    # このノードは応答を生成せず、次のsummarize_plan_nodeに処理を繋げる
    return state

def summarize_plan_node(state: AgentState) -> AgentState:
    """現在の計画詳細を取得し、LLMで自然な要約文を生成する。"""
    print("Executing summarize_plan_node with LLM...")
    plan_id = state.get("activePlanId")
    if not plan_id:
        state["finalResponse"] = "エラー：対象の計画が見つかりません。"
        return state

    with session_scope() as db:
        plan = itinerary_service.get_plan_details(db, plan_id=plan_id)
    
    if not plan or not plan.stops:
        state["finalResponse"] = "計画にはまだ何も登録されていません。どこか追加しますか？"
        return state

    summary_text = llm_service.generate_plan_summary(
        stops=plan.stops,
        language=state["language"]
    )
    
    state["finalResponse"] = summary_text or "計画の要約を作成できませんでした。"
    return state