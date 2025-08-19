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
# STEP 1: 意図分類ノード
# =========================

def information_entry(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    情報提供フローのエントリーポイント。
    ユーザーの最新メッセージから意図を分類し、後続のRAGで利用する情報をstateに格納する。
    """
    lang: str = state.get("lang", "ja")
    latest_user_message: str = state.get("latest_user_message", "").strip()
    llm = LLMInferenceService()

    try:
        # LLMで意図を分類
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

        # 分類結果を後続処理で使いやすい形式にマッピング
        intent_type, query_for_search = _map_intent_for_information(intent_result, latest_user_message)
        
        # stateを更新
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
        state["app_status"] = "error" # エラーステータスへ

    return state


# =========================
# STEP 2: RAG・ナッジ情報収集ノード
# =========================

def gather_nudge_and_pick_best(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    意図分類の結果に基づき、候補スポットとナッジ材料（天気、混雑等）を収集し、
    最適なものを評価・選択してstateに格納する。
    """
    # 前のステップでエラーが発生していたら、このノードはスキップ
    if state.get("app_status") == "error":
        return state

    lang: str = state.get("lang", "ja")
    intent_type: str = state.get("intent_type", "general_tourist")
    query_for_search: str = state.get("intent_query", "")
    info = InformationService()
    llm = LLMInferenceService()

    try:
        date_range = _get_date_range_from_state(state)
        user_location = _get_user_location_from_state(state)

        # 意図に基づき候補スポットをDBから検索
        spots = info.find_spots_by_intent(
            intent=intent_type,
            query_text=query_for_search,
            language=lang,
        )
        
        if not spots:
            # 候補が見つからない場合も応答を生成して終了
            state["final_response"] = "申し訳ありません、ご要望に合う場所を見つけられませんでした。別の探し方を試しましょうか？"
            state["app_status"] = "information"
            return state

        # ナッジ材料（距離/時間、日別天気、混雑→最適日）を収集
        spot_ids = [s.get("id") for s in spots if s.get("id")]
        materials = info.find_best_day_and_gather_nudge_data(
            spot_ids=spot_ids,
            start_date=date.fromisoformat(date_range["start"]),
            end_date=date.fromisoformat(date_range["end"]),
            origin_lat=user_location.get("lat") if user_location else None,
            origin_lon=user_location.get("lon") if user_location else None,
            lang=lang,
        )

        # 固有名詞質問の場合、追加で詳細情報（説明文、社会的証明など）を取得
        spot_details_map = {}
        if intent_type == "specific":
            for s in spots:
                spot_id = s.get("id")
                if not spot_id: continue
                try:
                    d = info.get_spot_details(spot_id)
                    spot_details_map[spot_id] = {
                        "official_name": d.get("official_name"),
                        "description": d.get("description"),
                        "social_proof": d.get("social_proof"), # このキーは現状get_spot_detailsにないが将来用に残す
                    }
                except Exception:
                    pass

        # stateを更新
        state["candidate_spots"] = spots # ORMオブジェクトではなく辞書を格納
        state["nudge_materials"] = materials
        state["spot_details"] = spot_details_map
        state["date_range"] = date_range
        state["user_location"] = user_location

    except Exception as e:
        # 情報収集に失敗した場合のエラーメッセージ
        try:
            err_text = llm.generate_error_message(
                context={"phase": "nudge_materials", "error": str(e)},
                lang=lang,
            )
        except Exception:
            err_text = "うまく候補の情報を集められませんでした。別の条件でもう一度試しましょうか？"
        state["final_response"] = err_text
        state["app_status"] = "error"

    return state


# =========================
# STEP 3: 応答生成ノード
# =========================

def compose_nudge_response(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    収集した情報と長期記憶を元に、最終的なナッジ提案文を生成する。
    """
    # 前のステップでエラーまたは候補なし応答が設定されていたらスキップ
    if state.get("app_status") == "error" or state.get("final_response"):
        return state
        
    lang: str = state.get("lang", "ja")
    session_id: Optional[str] = state.get("session_id")
    latest_user_message: str = state.get("latest_user_message", "")
    emb = EmbeddingService()
    llm = LLMInferenceService()

    # 長期記憶（会話履歴のKNN検索）を注入
    try:
        long_term_context = []
        if session_id and latest_user_message:
            similar_messages = emb.knn_messages(
                session_id=session_id,
                query_text=latest_user_message,
                k=5,
                lang=lang,
            )
            for m in similar_messages:
                long_term_context.append({
                    "speaker": m.get("speaker"),
                    "text": m.get("text"),
                    "ts": m.get("ts"),
                })
        state["long_term_context"] = long_term_context
    except Exception:
        state["long_term_context"] = [] # 失敗は許容

    # LLMに渡す最終的なコンテキストを構築
    try:
        context_payload = {
            "spots": state.get("candidate_spots", []),
            "materials": state.get("nudge_materials", {}),
            "spot_details": state.get("spot_details", {}),
            "long_term_context": state.get("long_term_context", []),
            "user_query": latest_user_message,
            "date_range": state.get("date_range"),
            "user_location": state.get("user_location"),
            "today": _today_str(),
        }

        # LLMを呼び出して応答文を生成
        text = _safe_call_generate_nudge(llm, context_payload, lang=lang)
        
        # stateを更新
        state["final_response"] = text
        state["app_status"] = "information"

    except Exception as e:
        # 最終生成に失敗した場合のエラーメッセージ
        try:
            err_text = llm.generate_error_message(
                context={"phase": "nudge_generation", "error": str(e)},
                lang=lang,
            )
        except Exception:
            err_text = "最終文面の生成に失敗しました。条件を少し変えてもう一度試しましょう。"
        state["final_response"] = err_text
        state["app_status"] = "error"

    return state