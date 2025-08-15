# -*- coding: utf-8 -*-
"""
itinerary_nodes.py
- 計画フロー：CRUD → 暫定ルート計算 → LLM要約
"""

from __future__ import annotations
from typing import Dict, Any, List, Optional

from worker.app.services.itinerary.itinerary_service import ItineraryService
from worker.app.services.routing.routing_service import RoutingService
from worker.app.services.llm.llm_service import LLMInferenceService
from ..state import AgentState, save_message, persist_session_status
from .shared_nodes import safe_set_final_response


def upsert_plan(state: AgentState) -> AgentState:
    """
    「計画の作成/編集」要求をLLMでパラメータ抽出し、Itinerary ServiceのCRUDに委譲。
    - action: add/remove/reorder/create
    """
    llm = LLMInferenceService()
    itin = ItineraryService()

    user_msg = state.latest_user_message or ""
    current_stops = []
    if state.active_plan_id:
        current_stops = itin.get_plan_stops(state.active_plan_id)

    params = llm.extract_plan_edit_parameters(
        user_message=user_msg,
        current_stops=current_stops,
        lang=state.user_lang,
    )

    # plan がなければ作る
    plan_id = state.active_plan_id
    if not plan_id:
        plan_id = itin.create_new_plan(session_id=state.session_id)
        state.active_plan_id = plan_id
        persist_session_status(state.session_id, app_status="planning", active_plan_id=plan_id)

    # action適用
    if params.action == "add" and params.spot_name:
        itin.add_spot_to_plan(plan_id, params.spot_name, position_hint=params.position, target_name=params.target_spot_name)
    elif params.action == "remove" and params.spot_name:
        itin.remove_spot_from_plan(plan_id, params.spot_name)
    elif params.action == "reorder" and params.spot_name and params.target_spot_name:
        itin.reorder_plan_stops(plan_id, params.spot_name, params.position, params.target_spot_name)
    # "create" 等、その他は現状ノーオペ

    return state


def calc_preview_route_and_summarize(state: AgentState) -> AgentState:
    """
    計画の暫定ルートをRouting Serviceで計算し、LLMでテキスト要約。
    """
    itin = ItineraryService()
    routing = RoutingService()
    llm = LLMInferenceService()

    if not state.active_plan_id:
        safe_set_final_response(state, "計画が見つかりませんでした。まずは訪問先を追加してみましょう。")
        return state

    stops: List[Dict[str, Any]] = itin.get_plan_stops(state.active_plan_id)
    if not stops:
        safe_set_final_response(state, "計画にスポットがありません。追加してから再度お試しください。")
        return state

    waypoints = [(s["lon"], s["lat"]) for s in stops]  # OSRMは(lon,lat)
    geojson = routing.calculate_full_itinerary_route(waypoints=waypoints, profile="car", add_return=True)  # FR-5-2

    # DBに暫定ルートが必要なら ItineraryService 側で保存する運用でもOK（ここではレスポンスのみに保持）
    state.bag["preview_geojson"] = geojson

    summary = llm.generate_plan_summary(
        lang=state.user_lang,
        stops=stops,
    )
    state.final_response = summary
    save_message(state.session_id, "assistant", summary, meta={"type": "plan_summary"})

    return state
