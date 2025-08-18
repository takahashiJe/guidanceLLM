# -*- coding: utf-8 -*-
"""
Information Flow の各ノード群（長期記憶注入対応版）
- 役割:
  - ユーザー意図に応じたスポット候補抽出
  - ナッジ材料（距離/天気/混雑）収集と最適日の算出
  - LLM による最終提案文生成
  - ★ 長期記憶（会話 Embedding）の検索結果を LLM に渡す（フェーズ10-2）

前提:
- 短期記憶は Orchestrator の state.py で直近5往復を取得済み。
- 長期記憶は shared.app.embeddings で埋め込み/類似度計算し、
  ConversationMemory（models.ConversationMemory）に保存済み（フェーズ10-1/10-3）。

このファイルでは、"最新ユーザー発話" をクエリとして長期記憶から
関連が高いメモリを取り出し、LLM のプロンプトに注入する。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from shared.app.database import SessionLocal
from shared.app import models

from worker.app.services.embeddings import embed_text, cosine_similarities
from worker.app.services.information.information_service import InformationService
from worker.app.services.routing.routing_service import RoutingService
from worker.app.services.llm.llm_service import LLMInferenceService


# =========================
# 長期記憶の取得ヘルパ
# =========================

def _fetch_long_term_memories(
    *,
    db,
    user_id: Optional[int],
    session_id: str,
    lang: str,
    query_text: str,
    top_k: int = 5,
    min_sim: float = 0.25,
) -> List[Dict[str, Any]]:
    """
    会話埋め込みテーブル（ConversationMemory）から k近傍検索を行う。
    - ユーザー単位（user_id優先）で取得、見つからない場合は session_id でフォールバック
    - lang は一致優先。空であれば無条件に候補。
    - DB から一定件数をロードし、Python側でコサイン類似度を計算して上位を返す。
    """
    if not query_text or not query_text.strip():
        return []

    # クエリの埋め込み
    q_emb = np.array(embed_text(query_text), dtype=np.float32)

    # まず user_id で検索、無ければ session_id で検索
    q = db.query(models.ConversationMemory)
    if user_id is not None:
        q = q.filter(models.ConversationMemory.user_id == user_id)
    else:
        q = q.filter(models.ConversationMemory.session_id == session_id)

    # 言語が指定されている場合は一致優先
    if lang:
        q = q.filter(models.ConversationMemory.lang == lang)

    # 最近のものから上限件数をサンプリング（性能と精度の折衷）
    # 必要に応じて上限を .env や定数に
    candidates: List[models.ConversationMemory] = (
        q.order_by(models.ConversationMemory.ts.desc()).limit(1000).all()
    )
    if not candidates:
        return []

    # ベクトルとテキストを取得
    mats = []
    texts = []
    for row in candidates:
        if row.embedding is None:
            continue
        try:
            vec = np.array(row.embedding, dtype=np.float32)
            if vec.ndim != 1:
                continue
            mats.append(vec)
            texts.append(row.text or "")
        except Exception:
            continue

    if not mats:
        return []

    mat = np.vstack(mats)
    sims = cosine_similarities(q_emb, mat)  # shape: (N, )

    # スコア上位 top_k、かつ閾値以上のみ返却
    idx_sorted = np.argsort(-sims)
    results: List[Dict[str, Any]] = []
    for idx in idx_sorted[:top_k * 2]:  # 少し多めに取り、閾値で最終絞り込み
        score = float(sims[idx])
        if score < min_sim:
            continue
        results.append({"score": score, "text": texts[idx]})
        if len(results) >= top_k:
            break

    return results


def _format_long_term_context_for_prompt(memories: List[Dict[str, Any]]) -> str:
    """
    LLM プロンプトに埋め込むための長期記憶テキスト整形。
    """
    if not memories:
        return ""
    lines = []
    for m in memories:
        # スコアは小数2桁で参考表示（必要なければ省略可）
        lines.append(f"- ({m['score']:.2f}) {m['text']}")
    return "\n".join(lines)


# =========================
# 既存フローのノード（例）
# =========================

def node_find_spots_by_intent(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    情報提供フェーズ最初のノード：
      - ユーザー意図に応じた候補スポットを抽出
    期待入力:
      state["intent_type"], state["query"], state["lang"]
    追記:
      - 変更なし（既存仕様通り）
    """
    lang = state.get("lang", "ja")
    intent_type = state.get("intent_type")  # "specific" | "category" | "general_tourist"
    query = state.get("query") or state.get("latest_user_message") or ""

    info = InformationService()
    with SessionLocal() as db:
        spots = info.find_spots_by_intent(db=db, intent_type=intent_type, query=query, language=lang)
    state["candidate_spots"] = spots
    return state


def node_gather_nudge_materials(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    候補スポットに対して、距離/天気/混雑を収集し最適日を決定。
    期待入力:
      state["candidate_spots"], state["user_location"], state["date_range"]
    追記:
      - 変更なし（既存仕様通り）
    """
    spots = state.get("candidate_spots") or []
    user_location = state.get("user_location") or {}
    date_range = state.get("date_range") or {}
    if not spots:
        state["nudge_materials"] = {}
        return state

    info = InformationService()
    with SessionLocal() as db:
        materials = info.find_best_day_and_gather_nudge_data(
            db=db,
            spots=spots,
            user_location=user_location,
            date_range=date_range,
        )
    state["nudge_materials"] = materials
    return state


def node_generate_nudge_text(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    ナッジ材料を元に、LLM で最終提案文を生成。
    ★ ここで「長期記憶」をプロンプトに注入する（フェーズ10-2）
    期待入力:
      state["nudge_materials"], state["lang"], state["latest_user_message"],
      state["session_id"], state["user_id"]
    出力:
      state["final_response"]
    """
    lang = state.get("lang", "ja")
    latest = state.get("latest_user_message", "")
    session_id = state.get("session_id")
    user_id = state.get("user_id")

    # 1) 長期記憶の検索
    with SessionLocal() as db:
        memories = _fetch_long_term_memories(
            db=db,
            user_id=user_id,
            session_id=session_id,
            lang=lang,
            query_text=latest,
            top_k=5,
            min_sim=0.25,
        )
    long_term_context = _format_long_term_context_for_prompt(memories)

    # 2) LLM で提案文生成（長期記憶を注入）
    llm = LLMInferenceService()
    nudge_materials = state.get("nudge_materials") or {}
    text = llm.generate_nudge_proposal(
        lang=lang,
        user_message=latest,
        nudge_materials=nudge_materials,
        long_term_context=long_term_context,  # ★ 追加
    )
    state["final_response"] = text
    return state
