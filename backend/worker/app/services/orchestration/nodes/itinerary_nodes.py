# worker/app/services/orchestration/nodes/itinerary_nodes.py
# （現段階ではスタブ（骨格）のみ実装）

from shared.app.schemas import AgentState

def create_plan_node(state: AgentState) -> AgentState:
    print("Executing create_plan_node...")
    # TODO: ItineraryServiceを呼び出して新しい計画を作成する
    # state['activePlanId'] = new_plan_id
    # state['appStatus'] = 'planning'
    state["finalResponse"] = "新しい旅行計画を作成しますね。どこに行きたいですか？"
    return state

def summarize_plan_node(state: AgentState) -> AgentState:
    print("Executing summarize_plan_node...")
    # TODO: ItineraryServiceから計画詳細を取得し、LLMで要約する
    state["finalResponse"] = "現在の計画は、[スポットA]、[スポットB]の順に巡る予定です。次に何をしますか？"
    return state