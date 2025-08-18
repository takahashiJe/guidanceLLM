# -*- coding: utf-8 -*-
"""
Information Flow の各ノード群
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
from datetime import date, timedelta

from shared.app.database import SessionLocal
from shared.app import models

from worker.app.services.embeddings import embed_text, cosine_similarities
from worker.app.services.information.information_service import InformationService
from worker.app.services.routing.routing_service import RoutingService
from worker.app.services.llm.llm_service import LLMInferenceService

try:
    # ナッジ集計の本実装：これまで積み上げた天気/混雑/距離スコアや山判定、DB 検索などを内包
    from worker.app.services.information.information_service import InformationService
except Exception as e:  # pragma: no cover
    # ここで失敗すると worker 起動自体が止まるため、明示的に例外化
    raise ImportError("InformationService の import に失敗しました") from e


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

def information_entry(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    情報探索フェーズの入口ノード。
    - state に intermediate（中間ワーク領域）が無ければ初期化
    - そのまま次のノード（find_candidate_spots_node 等）に受け渡す
    以降の分岐は Graph 側のエッジ定義（router/graph）で制御される。
    """
    if state.get("intermediate") is None:
        state["intermediate"] = {}
    return state


def _extract_spot_ids_from_state(state: Dict[str, Any]) -> List[int]:
    """
    state から頑健に Spot ID 群を取り出す。
    - selected_spot_ids / spot_ids: すでに ID 配列が入っているケース
    - selected_spots / spots: dict or ORM オブジェクト配列から id を抽出
    """
    if not isinstance(state, dict):
        return []

    # 最優先で ID 配列
    for key in ("selected_spot_ids", "spot_ids"):
        ids = state.get(key)
        if isinstance(ids, list) and ids:
            return [int(x) for x in ids if x is not None]

    # オブジェクト配列から抽出
    for key in ("selected_spots", "spots"):
        arr = state.get(key)
        if isinstance(arr, list) and arr:
            out: List[int] = []
            for s in arr:
                if isinstance(s, dict):
                    sid = s.get("id")
                else:
                    sid = getattr(s, "id", None)
                if sid is not None:
                    out.append(int(sid))
            if out:
                return out

    return []


def gather_nudge_and_pick_best(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    graph.py が import する想定のノード関数（後方互換用の公開名）。
    既存の InformationService.find_best_day_and_gather_nudge_data を呼び出し、
    state へ必要最小の差分を返す。

    入力（state から参照する代表的キー）:
      - selected_spot_ids / spot_ids / selected_spots / spots
      - date_from / date_to （無ければ [今日, 今日+6日] を使用）
      - lang / language      （無ければ "ja"）

    返り値（state への差分）:
      - nudge:  per-day の詳細や採用根拠を含む辞書（既存サービスの戻りを丸ごと格納）
      - best_day: nudge["best_day"]
      - nudge_per_day: nudge["per_day"]
    """
    spot_ids = _extract_spot_ids_from_state(state)
    if not spot_ids:
        # スポット候補がなければ何もしない（上流で分岐する想定）
        return {"nudge": None, "best_day": None, "nudge_per_day": []}

    # 日付レンジの既定値（実運用に耐える本実装として 1 週間スコープを採用）
    today = date.today()
    date_from = state.get("date_from") or today
    date_to = state.get("date_to") or (today + timedelta(days=6))

    lang = state.get("lang") or state.get("language") or "ja"

    svc = InformationService()
    nudge = svc.find_best_day_and_gather_nudge_data(
        spot_ids=spot_ids,
        date_from=date_from,
        date_to=date_to,
        lang=lang,
    )

    # 既存の下流ノードで利用される最小差分のみ state 反映
    return {
        "nudge": nudge,
        "best_day": nudge.get("best_day"),
        "nudge_per_day": nudge.get("per_day") or nudge.get("per_day_details") or [],
    }


# 明示的に公開シンボルへ追加（他の __all__ の定義を壊さない）
try:
    __all__  # type: ignore  # noqa
except NameError:  # pragma: no cover
    __all__ = []
if "gather_nudge_and_pick_best" not in __all__:
    __all__.append("gather_nudge_and_pick_best")