# -*- coding: utf-8 -*-
"""
information_nodes.py
- 情報提供フロー：候補抽出→ナッジ材料収集→最適提案文（LLM）
"""

from __future__ import annotations
from typing import Any, Dict, List

from worker.app.services.information.information_service import InformationService
from worker.app.services.llm.llm_service import LLMInferenceService
from .shared_nodes import safe_set_final_response
from ..state import AgentState, save_message


def information_entry(state: AgentState) -> AgentState:
    """
    情報提供フローのエントリ。
    意図分類の結果に応じて、find_spots_by_intent の intent_type を決定し候補抽出。
    """
    info = InformationService()
    llm = LLMInferenceService()

    user_msg = state.latest_user_message or ""
    # classify は router 済みだが、intentの詳細は LLM から再取得する方が堅牢
    intent = llm.classify_intent(
        user_message=user_msg,
        app_status=state.app_status,
        history=[m.model_dump() for m in state.short_history],
        lang=state.user_lang,
    )

    # intent_type を Information Service の規約に合わせて決定
    if intent.intent == "specific_question":
        intent_type = "specific"
        query = intent.parameters.get("entity_name") or user_msg
    elif intent.intent == "general_question":
        # 「どこか良いところ」=> tourist_spot 固定
        intent_type = "general_tourist"
        query = intent.parameters.get("category") or user_msg
    else:
        # それ以外はひとまずカテゴリ推測
        intent_type = "category"
        query = intent.parameters.get("category") or user_msg

    spots = info.find_spots_by_intent(
        intent_type=intent_type,
        query=query,
        language=state.user_lang,
    )

    state.bag["candidate_spots"] = [s.id for s in spots]
    return state


def gather_nudge_and_pick_best(state: AgentState) -> AgentState:
    """
    候補全件に対して：
      - get_distance_and_duration（routing連携）
      - 天気（山タグ→crawler→fallback API）
      - 混雑（Itinerary Service のJOIN集計）
    を収集・スコアし、スポット毎の最適日を決定。
    その中から総合的に最適なスポットを1つ選ぶ。
    """
    info = InformationService()

    spot_ids: List[int] = state.bag.get("candidate_spots", [])
    if not spot_ids:
        # 候補ゼロならここで終了
        safe_set_final_response(state, "該当する候補を見つけられませんでした。別の条件でお試しください。")
        return state

    # フロント側でユーザー現在地を送ってきている想定なら state.bag に入っている
    # なければ便宜的に None を渡す（Information Service 側で扱えるようにしておく）
    user_location = state.bag.get("user_location")
    date_range = state.bag.get("date_range")  # {"start": "YYYY-MM-DD", "end":"YYYY-MM-DD"} を想定

    # Information Service でナッジ材料を収集し、最適日を決定
    result: Dict[int, Dict[str, Any]] = info.find_best_day_and_gather_nudge_data(
        spot_ids=spot_ids, user_location=user_location, date_range=date_range
    )
    state.bag["nudge_materials"] = result

    # 単純にスコア（Information Service 側で持たせた total_score）最大のスポットを選ぶ
    best_spot_id = None
    best_score = -1
    for sid, payload in result.items():
        score = payload.get("total_score", -1)
        if score > best_score:
            best_score = score
            best_spot_id = sid

    state.bag["best_spot_id"] = best_spot_id
    return state


def compose_nudge_response(state: AgentState) -> AgentState:
    """
    ベストスポットの詳細を取得し、LLMで「説得力あるナッジ応答」を生成。
    """
    info = InformationService()
    llm = LLMInferenceService()

    best_spot_id = state.bag.get("best_spot_id")
    nudge = state.bag.get("nudge_materials", {}).get(best_spot_id)
    if not best_spot_id or not nudge:
        safe_set_final_response(state, "良さそうな候補を特定できませんでした。条件を変えてもう一度お試しください。")
        return state

    spot = info.get_spot_details(spot_id=best_spot_id)
    text = llm.generate_nudge_proposal(
        lang=state.user_lang,
        spot={
            "official_name": spot.official_name,
            "description": spot.description,
            "social_proof": spot.social_proof,
        },
        materials=nudge,
    )

    # 応答を最終レスポンスに反映
    state.final_response = text

    # 履歴保存（assistant）
    save_message(state.session_id, "assistant", text, meta={"type": "nudge"})
    return state
