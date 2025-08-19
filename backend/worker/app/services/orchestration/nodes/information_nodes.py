# backend/worker/app/services/orchestration/nodes/information_nodes.py
# -*- coding: utf-8 -*-
"""
情報提供フェーズのノード群（LangGraph ノード）。
NLU → RAG（候補抽出/最適日・材料）→ 長期記憶注入 → 生成 の順序を担保する。

依存サービス:
- InformationService: 候補スポット抽出、ナッジ材料取得（距離/時間・天気[山はcrawler→fallback API]・混雑[MView]）
- EmbeddingService: 会話の長期記憶（KNN）注入
- LLMInferenceService: 意図分類、最終ナッジ文生成、エラー文生成

本ファイルは既存の呼び出し互換性のため、複数のエイリアス関数
(information_flow, run_information_pipeline 等) を用意し、内部で単一路線に集約する。
"""

from __future__ import annotations

from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional, Tuple

# サービス依存
from worker.app.services.information.information_service import InformationService
from worker.app.services.llm.llm_service import LLMInferenceService
from worker.app.services.embeddings import EmbeddingService


# =========================
# 内部ユーティリティ
# =========================

def _today_str() -> str:
    return date.today().isoformat()


def _default_date_range(days: int = 7) -> Dict[str, str]:
    """デフォルトで今日〜7日後の範囲を返す（ISO文字列）。"""
    start = date.today()
    end = start + timedelta(days=days)
    return {"start": start.isoformat(), "end": end.isoformat()}


def _get_date_range_from_state(state: Dict[str, Any]) -> Dict[str, str]:
    """
    state から日付範囲を推定。オーケストレータで解釈済みならそれを尊重し、
    無ければデフォルト7日間。
    例: state["agent_state"]["desired_date_range"] or state["candidate_date_range"]
    """
    agent_state = state.get("agent_state") or {}
    # 代表的なキーを順に探索
    for key in ("desired_date_range", "candidate_date_range", "date_range"):
        dr = agent_state.get(key) or state.get(key)
        if isinstance(dr, dict) and "start" in dr and "end" in dr:
            return {"start": str(dr["start"]), "end": str(dr["end"])}
    return _default_date_range()


def _get_user_location_from_state(state: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """
    state からユーザ位置を推定。ナビ/フロントから注入済みが理想。
    無い場合は None を返し、InformationService 側で None を許容（距離/時間は欠損）。
    """
    agent_state = state.get("agent_state") or {}
    for key in ("user_location", "last_known_location", "current_location"):
        loc = agent_state.get(key) or state.get(key)
        if isinstance(loc, dict) and "lat" in loc and "lon" in loc:
            try:
                return {"lat": float(loc["lat"]), "lon": float(loc["lon"])}
            except Exception:
                pass
    return None


def _map_intent_for_information(intent_result: Dict[str, Any], fallback_text: str) -> Tuple[str, str]:
    """
    LLM の意図分類結果から InformationService の intent_type とクエリ文字列を決める。
    - intent_type: "specific" | "category" | "general_tourist"
    - query: specific/category の場合に活用する文字列
    """
    # できるだけ汎用的に（キー名が多少違っても拾えるように）
    intent = (intent_result.get("intent")
              or intent_result.get("category")
              or intent_result.get("label")
              or "").lower()

    # specific のターゲット候補を吸い上げ
    target_spot = (intent_result.get("target_spot_name")
                   or intent_result.get("spot_name")
                   or intent_result.get("entity")
                   or "").strip()

    category = (intent_result.get("category_name")
                or intent_result.get("tag")
                or intent_result.get("topic")
                or "").strip()

    # マッピング
    if "specific" in intent or "spot_specific" in intent or "proper_noun" in intent:
        intent_type = "specific"
        query = target_spot or fallback_text
    elif "category" in intent:
        intent_type = "category"
        query = category or fallback_text
    elif "general" in intent or "tourist" in intent or "broad" in intent:
        intent_type = "general_tourist"
        query = ""
    else:
        # よくあるカテゴリ（general_question, specific_question など）にも耐える
        if "specific_question" in intent:
            intent_type = "specific"
            query = target_spot or fallback_text
        elif "category_question" in intent:
            intent_type = "category"
            query = category or fallback_text
        else:
            intent_type = "general_tourist"
            query = ""

    return intent_type, query


def _safe_call_generate_nudge(llm: LLMInferenceService, payload: Dict[str, Any], lang: str) -> str:
    """
    LLMInferenceService.generate_nudge_proposal のインターフェース差異に耐えるため、
    安全に呼び出すユーティリティ。想定の複数シグネチャを順に試みる。
    """
    # 1) まず context 込みの2引数: (context: dict, lang: str)
    try:
        return llm.generate_nudge_proposal(payload, lang)
    except TypeError:
        pass

    # 2) キーワード引数版: (context=..., lang=...)
    try:
        return llm.generate_nudge_proposal(context=payload, lang=lang)
    except TypeError:
        pass

    # 3) フラット引数版（spots/materials/long_term_context/... 個別指定）
    try:
        return llm.generate_nudge_proposal(
            spots=payload.get("spots"),
            materials=payload.get("materials"),
            long_term_context=payload.get("long_term_context"),
            user_query=payload.get("user_query"),
            date_range=payload.get("date_range"),
            user_location=payload.get("user_location"),
            lang=lang,
        )
    except TypeError:
        pass

    # 4) 最後のフォールバック：テキスト化して投げる
    try:
        return llm.generate_nudge_proposal(str(payload), lang)
    except Exception as e:
        # どうしてもダメなら呼び出し元でエラー文生成へ
        raise e


# =========================
# コア・パイプライン
# =========================

def _run_information_pipeline(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    情報提供フェーズの中核。NLU → 候補/材料 → 長期記憶注入 → 生成
    """
    lang: str = state.get("lang", "ja")
    session_id: Optional[str] = state.get("session_id")
    latest_user_message: str = state.get("latest_user_message", "").strip()

    info = InformationService()
    llm = LLMInferenceService()
    emb = EmbeddingService()

    # ----------------------
    # 1) NLU: 意図分類
    # ----------------------
    try:
        # できるだけ寛容に: 返り値が Pydantic / dict / str のいずれでも対応
        intent_result = llm.classify_intent(
            latest_message=latest_user_message,
            app_status=(state.get("agent_state") or {}).get("app_status"),
            chat_history=(state.get("agent_state") or {}).get("chat_history", []),
            lang=lang,
        )
        if hasattr(intent_result, "model_dump"):
            intent_result = intent_result.model_dump()
        elif not isinstance(intent_result, dict):
            intent_result = {"intent": str(intent_result)}

        intent_type, query_for_search = _map_intent_for_information(intent_result, latest_user_message)
        state["intent_result"] = intent_result
        state["intent_type"] = intent_type
        state["intent_query"] = query_for_search

    except Exception as e:
        # 意図分類に失敗した場合はエラーメッセージを生成して返す
        try:
            err_text = llm.generate_error_message(
                context={"phase": "intent_classification", "error": str(e), "user_message": latest_user_message},
                lang=lang,
            )
        except Exception:
            err_text = "うまく意図を理解できませんでした。もう少し詳しく教えてください。"
        state["final_response"] = err_text
        state["app_status"] = "information"
        return state

    # ----------------------
    # 2) RAG: 候補抽出 → 最適日/材料 収集
    # ----------------------
    try:
        date_range = _get_date_range_from_state(state)
        user_location = _get_user_location_from_state(state)

        # 候補抽出
        spots = info.find_spots_by_intent(
            intent_type=intent_type,
            query=query_for_search,
            language=lang,
        )
        state["candidate_spots"] = [getattr(s, "id", None) for s in spots]

        # 材料（距離/時間、日別天気、混雑→最適日）
        materials = info.find_best_day_and_gather_nudge_data(
            spots=spots,
            user_location=user_location,
            date_range=date_range,
        )

        # 固有名詞質問の場合、詳細テキスト（official_name, description, social_proof）を追加で取得
        spot_details_map = {}
        if intent_type == "specific":
            for s in spots:
                try:
                    d = info.get_spot_details(getattr(s, "id"))
                    spot_details_map[getattr(s, "id")] = {
                        "official_name": getattr(d, "official_name", None),
                        "description": getattr(d, "description", None),
                        "social_proof": getattr(d, "social_proof", None),
                    }
                except Exception:
                    # 1件でも失敗しても他は継続
                    pass

        # 生成向けに state に格納（デバッグ/再利用用）
        state["nudge_materials"] = materials
        state["spot_details"] = spot_details_map
        state["date_range"] = date_range
        state["user_location"] = user_location

    except Exception as e:
        # 情報収集に失敗した場合はエラーメッセージを生成
        try:
            err_text = llm.generate_error_message(
                context={"phase": "nudge_materials", "error": str(e)},
                lang=lang,
            )
        except Exception:
            err_text = "うまく候補の情報を集められませんでした。別の条件でもう一度試しましょうか？"
        state["final_response"] = err_text
        state["app_status"] = "information"
        return state

    # ----------------------
    # 3) 長期記憶（会話のKNN）注入
    # ----------------------
    try:
        long_term = []
        if session_id and latest_user_message:
            long_term = emb.knn_messages(
                session_id=session_id,
                query_text=latest_user_message,
                k=5,
                lang=lang,
            )
        # LLM に渡しやすい形へ（speaker/text/ts）
        long_term_context = []
        for m in long_term:
            long_term_context.append({
                "speaker": m.get("speaker"),
                "text": m.get("text"),
                "ts": m.get("ts"),
            })
        state["long_term_context"] = long_term_context
    except Exception:
        # 長期記憶の失敗は致命ではないので無視（ログは上位で拾う想定）
        state["long_term_context"] = []

    # ----------------------
    # 4) 生成: 最終ナッジ提案文
    # ----------------------
    try:
        # LLMへ渡すコンテキストを1つにまとめる
        # InformationService の find_spots_by_intent は ORM オブジェクトを返す想定なので、
        # LLM に渡しやすい軽量データへ変換（id/official_name 程度）
        compact_spots = []
        for s in spots:
            compact_spots.append({
                "id": getattr(s, "id", None),
                "official_name": getattr(s, "official_name", None),
                "tags": getattr(s, "tags", None),
                "spot_type": getattr(s, "spot_type", None),
                "lat": getattr(s, "lat", None),
                "lon": getattr(s, "lon", None),
            })

        context_payload = {
            "spots": compact_spots,                         # 候補
            "materials": materials,                         # id -> {best_date, weather, congestion, distance_km, duration_min, ...}
            "spot_details": spot_details_map,               # id -> {official_name, description, social_proof}
            "long_term_context": state.get("long_term_context", []),
            "user_query": latest_user_message,
            "date_range": state.get("date_range"),
            "user_location": state.get("user_location"),
            "today": _today_str(),
        }

        text = _safe_call_generate_nudge(llm, context_payload, lang=lang)
        # 状態更新
        state["final_response"] = text
        state["app_status"] = "information"

    except Exception as e:
        try:
            err_text = llm.generate_error_message(
                context={"phase": "nudge_generation", "error": str(e)},
                lang=lang,
            )
        except Exception:
            err_text = "最終文面の生成に失敗しました。条件を少し変えてもう一度試しましょう。"
        state["final_response"] = err_text
        state["app_status"] = "information"

    return state


# =========================
# 公開ノード（エイリアス）
# =========================

def run_information_pipeline(state: Dict[str, Any]) -> Dict[str, Any]:
    """グラフから直接呼ばれるメイン関数（別名1）。"""
    return _run_information_pipeline(state)


def information_flow(state: Dict[str, Any]) -> Dict[str, Any]:
    """グラフから直接呼ばれるメイン関数（別名2）。"""
    return _run_information_pipeline(state)


def collect_candidates_and_materials(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    既存互換: 候補抽出と材料集めだけ先に行い、一時保存する。
    その後の長期記憶注入/生成は別ノードで行う場合に利用。
    """
    lang: str = state.get("lang", "ja")
    latest_user_message: str = state.get("latest_user_message", "").strip()
    llm = LLMInferenceService()

    # 意図のみ判定
    intent_result = llm.classify_intent(
        latest_message=latest_user_message,
        app_status=(state.get("agent_state") or {}).get("app_status"),
        chat_history=(state.get("agent_state") or {}).get("chat_history", []),
        lang=lang,
    )
    if hasattr(intent_result, "model_dump"):
        intent_result = intent_result.model_dump()
    elif not isinstance(intent_result, dict):
        intent_result = {"intent": str(intent_result)}

    intent_type, query_for_search = _map_intent_for_information(intent_result, latest_user_message)
    state["intent_result"] = intent_result
    state["intent_type"] = intent_type
    state["intent_query"] = query_for_search

    # 候補・材料
    info = InformationService()
    date_range = _get_date_range_from_state(state)
    user_location = _get_user_location_from_state(state)

    spots = info.find_spots_by_intent(intent_type=intent_type, query=query_for_search, language=lang)
    materials = info.find_best_day_and_gather_nudge_data(spots=spots, user_location=user_location, date_range=date_range)

    spot_details_map = {}
    if intent_type == "specific":
        for s in spots:
            try:
                d = info.get_spot_details(getattr(s, "id"))
                spot_details_map[getattr(s, "id")] = {
                    "official_name": getattr(d, "official_name", None),
                    "description": getattr(d, "description", None),
                    "social_proof": getattr(d, "social_proof", None),
                }
            except Exception:
                pass

    state["candidate_spots"] = [getattr(s, "id", None) for s in spots]
    state["nudge_materials"] = materials
    state["spot_details"] = spot_details_map
    state["date_range"] = date_range
    state["user_location"] = user_location
    state["app_status"] = "information"
    return state


def inject_long_term_context(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    既存互換: 長期記憶のみ注入する段階関数。
    """
    lang: str = state.get("lang", "ja")
    session_id: Optional[str] = state.get("session_id")
    latest_user_message: str = state.get("latest_user_message", "").strip()

    try:
        emb = EmbeddingService()
        long_term = []
        if session_id and latest_user_message:
            long_term = emb.knn_messages(
                session_id=session_id,
                query_text=latest_user_message,
                k=5,
                lang=lang,
            )
        state["long_term_context"] = [
            {"speaker": m.get("speaker"), "text": m.get("text"), "ts": m.get("ts")}
            for m in long_term
        ]
    except Exception:
        state["long_term_context"] = []

    state["app_status"] = "information"
    return state


def generate_information_reply(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    既存互換: 材料と長期記憶が state に揃っている前提で、最終文面だけを生成。
    """
    lang: str = state.get("lang", "ja")
    llm = LLMInferenceService()

    # 必要なキーを取り出して payload を組む
    materials = state.get("nudge_materials") or {}
    spot_details_map = state.get("spot_details") or {}
    date_range = state.get("date_range") or _default_date_range()
    user_location = state.get("user_location")
    latest_user_message = state.get("latest_user_message", "")
    long_term_context = state.get("long_term_context", [])

    # 可能なら候補の軽量表現も作る（id/official_name 等）
    compact_spots = []
    candidate_ids = state.get("candidate_spots") or []
    # candidate_ids は ID 群のみなので、details から official_name を補う
    for sid in candidate_ids:
        details = spot_details_map.get(sid, {})
        compact_spots.append({
            "id": sid,
            "official_name": details.get("official_name"),
        })

    context_payload = {
        "spots": compact_spots,
        "materials": materials,
        "spot_details": spot_details_map,
        "long_term_context": long_term_context,
        "user_query": latest_user_message,
        "date_range": date_range,
        "user_location": user_location,
        "today": _today_str(),
    }

    try:
        text = _safe_call_generate_nudge(llm, context_payload, lang=lang)
        state["final_response"] = text
    except Exception as e:
        try:
            err_text = llm.generate_error_message(
                context={"phase": "nudge_generation", "error": str(e)},
                lang=lang,
            )
        except Exception:
            err_text = "最終文面の生成に失敗しました。条件を少し変えてもう一度試しましょう。"
        state["final_response"] = err_text

    state["app_status"] = "information"
    return state