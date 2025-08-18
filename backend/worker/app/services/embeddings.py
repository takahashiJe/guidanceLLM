# backend/worker/app/services/embeddings.py
# -*- coding: utf-8 -*-
"""
Embeddings（会話の長期記憶/RAG埋め込み）サービスの本実装。

設計ポイント
- 低レイヤの Ollama クライアント、DB 層(pgvector)、ベクタストア用埋め込み関数、
  そして公開 Facade（EmbeddingsService）を単一ファイルに内包しつつ責務分離。
- 既存呼び出し（state.py / information_nodes.py / 01_build_knowledge_graph.py）との互換性維持。
- Cosine 距離で KNN 検索（pgvector）。L2 正規化を実施。
- LRU キャッシュで同一テキストの埋め込み再計算を抑制。
- 例外・再試行・タイムアウトを備えた堅牢化。
"""

from __future__ import annotations

import os
import math
import time
import json
import logging
import hashlib
import functools
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

# pgvector の SQLAlchemy 型
try:
    from pgvector.sqlalchemy import Vector
except Exception:
    # ランタイムで未導入の環境でも import エラーで死なないようにする（実運用では pgvector を入れること）
    Vector = None  # type: ignore

# 共有の DB セッションファクトリ
from shared.app.database import SessionLocal
from shared.app import models

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ============================================
# 環境変数（デフォルト値を安全側に設定）
# ============================================
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", os.getenv("EMBED_MODEL", "mxbai-embed-large"))
EMBEDDING_VERSION = os.getenv("EMBEDDING_VERSION", f"{EMBEDDING_MODEL}@v1")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))

# ============================================
# ユーティリティ
# ============================================

def _l2_normalize(vec: Sequence[float]) -> List[float]:
    """L2 正規化（ゼロベクトルのときはそのまま返す）"""
    s = math.sqrt(sum((x * x) for x in vec))
    if s == 0:
        return list(vec)
    return [float(x) / s for x in vec]


def _sha_key(text: str) -> str:
    """テキストのキャッシュキー（先頭32桁で十分）"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


# ============================================
# 低レイヤ: Ollama Embeddings クライアント
# ============================================

class _OllamaEmbeddingsClient:
    """
    Ollama の /api/embeddings を叩くシンプルなクライアント。
    - リトライ（指数バックオフ＋ジッター）
    - タイムアウト
    - 応答検証（次元）
    - L2 正規化
    """
    def __init__(
        self,
        host: str = OLLAMA_HOST,
        model: str = EMBEDDING_MODEL,
        timeout: float = 30.0,
        max_retries: int = 3,
        embedding_dim: int = EMBEDDING_DIM,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.embedding_dim = embedding_dim
        self._session = requests.Session()
        self._url = f"{self.host}/api/embeddings"

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # リトライ（指数バックオフ＋小ジッター）
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                resp = self._session.post(self._url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                last_exc = e
                sleep_sec = min(2 ** attempt, 8) + (0.05 * attempt)
                logger.warning("Ollama embeddings request failed (attempt %s/%s): %s",
                               attempt + 1, self.max_retries, e)
                time.sleep(sleep_sec)
        # すべて失敗
        raise RuntimeError(f"Ollama embeddings request failed after retries: {last_exc}")

    def _post_and_extract(self, text: str) -> List[float]:
        # Ollama Embeddings API: {model, prompt}
        payload = {"model": self.model, "prompt": text}
        data = self._post(payload)
        # 期待されるキー: {"embedding": [...]} 形式
        if not isinstance(data, dict) or "embedding" not in data:
            raise ValueError(f"Invalid embeddings response: {data}")
        emb = data["embedding"]
        if not isinstance(emb, list) or len(emb) != self.embedding_dim:
            raise ValueError(
                f"Invalid embedding dimension: expected {self.embedding_dim}, got {len(emb)}"
            )
        return [float(x) for x in emb]

    @functools.lru_cache(maxsize=1024)
    def _embed_one_cached(self, key: str, text: str) -> Tuple[str, List[float]]:
        """LRU キャッシュ付き 1テキスト埋め込み（key はテキストハッシュ）"""
        emb = self._post_and_extract(text)
        emb = _l2_normalize(emb)
        return key, emb

    def embed_one(self, text: str) -> List[float]:
        """単一テキストを埋め込み（L2 正規化済み）"""
        key = _sha_key(text)
        _, emb = self._embed_one_cached(key, text)
        return emb

    def embed_many(self, texts: Iterable[str]) -> List[List[float]]:
        """
        複数テキストを埋め込み（API の単発呼び出しをループ。Ollama は配列入力未保証）
        - L2 正規化済みを返す
        """
        result: List[List[float]] = []
        for t in texts:
            result.append(self.embed_one(t))
        return result


# ============================================
# DB 層: 会話長期記憶（pgvector）
# ============================================

class _ConversationMemoryStore:
    """
    会話の長期記憶（conversation_message_embeddings テーブル）を司る DB 層。
    - upsert: (conversation_id, turn_id, speaker) で一意
    - knn: cosine 距離で近傍検索（L2 正規化済み前提）
    """
    def __init__(self, session_factory: Callable[[], Session] = SessionLocal) -> None:
        self._session_factory = session_factory

    def bulk_upsert(
        self,
        rows: List[Dict[str, Any]],
    ) -> None:
        """
        複数行を 1 トランザクションで UPSERT。
        rows の各要素：
          {
            "conversation_id": str,
            "turn_id": int,
            "speaker": str,  # "user" / "assistant" / ...
            "lang": str,
            "text": str,
            "embedding": List[float],
            "embedding_version": str,
            "ts": Optional[datetime]  # 省略可
          }
        """
        if not rows:
            return
        with self._session_factory() as db:
            try:
                # SQLAlchemy Core の insert ... on conflict do update を使う
                from sqlalchemy.dialects.postgresql import insert

                table = models.ConversationMessageEmbedding.__table__  # type: ignore
                stmt = insert(table).values(rows)
                # 一意制約 (conversation_id, turn_id, speaker) を前提
                on_conflict = stmt.on_conflict_do_update(
                    index_elements=["conversation_id", "turn_id", "speaker"],
                    set_={
                        "lang": stmt.excluded.lang,
                        "text": stmt.excluded.text,
                        "embedding": stmt.excluded.embedding,
                        "embedding_version": stmt.excluded.embedding_version,
                        "ts": stmt.excluded.ts,
                    },
                )
                db.execute(on_conflict)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("bulk_upsert failed")
                raise

    def knn_messages(
        self,
        conversation_id: str,
        query_embedding: List[float],
        k: int = 5,
        min_cosine: float = 0.2,
        role_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        KNN 検索（cosine 距離）。L2 正規化済みベクトル前提。
        - pgvector の演算子 `<=>` は「距離」。cosine 類似度 sim = 1 - distance として扱う。
        - min_cosine で下限フィルタ（SQL では距離 <= 1 - min_cosine）
        返却: [{speaker, text, lang, turn_id, ts, score}]
        """
        if Vector is None:
            raise RuntimeError("pgvector 'Vector' type is not available. Please install pgvector.")

        max_distance = 1.0 - float(min_cosine)
        # 念のため距離の上下限を安全側に丸める
        if max_distance < 0.0:
            max_distance = 0.0
        if max_distance > 2.0:
            max_distance = 2.0

        # SQL: 距離で ORDER BY。必要なら role_filter を付与。
        base_sql = """
            SELECT
                speaker,
                lang,
                text,
                turn_id,
                ts,
                (embedding <=> :query_vec) AS distance
            FROM conversation_message_embeddings
            WHERE conversation_id = :cid
              AND (embedding <=> :query_vec) <= :max_dist
        """
        if role_filter:
            base_sql += " AND speaker = :role "

        base_sql += " ORDER BY (embedding <=> :query_vec) ASC LIMIT :k "

        with self._session_factory() as db:
            # bindparam に Vector 型を指定（次元数から型を生成）
            if Vector is not None:
                vec_type = Vector(EMBEDDING_DIM)
            else:
                vec_type = None  # type: ignore

            params: Dict[str, Any] = {
                "cid": conversation_id,
                "query_vec": query_embedding,
                "max_dist": max_distance,
                "k": int(k),
            }
            if role_filter:
                params["role"] = role_filter

            stmt = text(base_sql).bindparams()
            # SQLAlchemy が pgvector を認識しやすいよう、bindparams で type_ を追加
            if vec_type is not None:
                # NOTE: bindparams の型指定（方言差によっては不要だが安全のため付ける）
                stmt = stmt.bindparams(
                    # type: ignore
                    text(":query_vec").bindparams(type_=vec_type)  # noqa
                )

            rows = db.execute(stmt, params).mappings().all()

        results: List[Dict[str, Any]] = []
        for r in rows:
            dist = float(r["distance"])
            # cosine 類似度に変換（sim = 1 - dist）
            sim = 1.0 - dist
            results.append(
                {
                    "speaker": r["speaker"],
                    "lang": r["lang"],
                    "text": r["text"],
                    "turn_id": int(r["turn_id"]),
                    "ts": r["ts"],
                    "score": sim,
                }
            )
        return results


# ============================================
# ベクタストア用 埋め込み関数アダプタ
# ============================================

class _VectorStoreEmbeddingFn:
    """
    ベクタストア側（Chroma など）の embedding_function に渡すためのコール可能オブジェクト。
    - テキスト配列を受け取り、L2 正規化済み埋め込み配列を返す。
    """
    def __init__(self, client: _OllamaEmbeddingsClient) -> None:
        self._client = client

    def __call__(self, texts: List[str]) -> List[List[float]]:
        return self._client.embed_many(texts)


# ============================================
# Facade: EmbeddingsService（公開 API）
# ============================================

class EmbeddingsService:
    """
    既存コードからの唯一の窓口。内部実装は各レイヤに委譲。
    - embed_text(text) -> List[float]
    - save_conversation_embeddings(session_id, turn_id, lang, user_text, assistant_text, embedding_version=None) -> None
    - knn_messages(session_id, query_text, k=5, min_cosine=0.2, role_filter=None) -> List[Dict]
    - format_memory_snippets(excerpts) -> str
    - embedding_function_for_vectorstore() -> Callable[[List[str]], List[List[float]]]
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        ollama_host: str = OLLAMA_HOST,
        model: str = EMBEDDING_MODEL,
        embedding_version: str = EMBEDDING_VERSION,
        embedding_dim: int = EMBEDDING_DIM,
    ) -> None:
        self._client = _OllamaEmbeddingsClient(
            host=ollama_host,
            model=model,
            embedding_dim=embedding_dim,
        )
        self._store = _ConversationMemoryStore(session_factory=session_factory)
        self._embedding_version = embedding_version
        self._embedding_dim = embedding_dim

    # -------------------------
    # 単発埋め込み（互換 API）
    # -------------------------
    def embed_text(self, text: str) -> List[float]:
        """
        1テキストを埋め込み（L2 正規化済み）。
        """
        if not isinstance(text, str):
            text = str(text)
        return self._client.embed_one(text)

    # ----------------------------------------
    # 会話（ユーザ/アシスタント）の埋め込みを一括保存
    # ----------------------------------------
    def save_conversation_embeddings(
        self,
        session_id: str,
        turn_id: int,
        lang: str,
        user_text: Optional[str],
        assistant_text: Optional[str],
        embedding_version: Optional[str] = None,
    ) -> None:
        """
        会話確定時に、ユーザ発話/アシスタント応答の埋め込みを一括保存する。
        - どちらかが None の場合でも問題ない（指定された方のみ保存）。
        - upsert（一意: conversation_id, turn_id, speaker）
        """
        rows: List[Dict[str, Any]] = []
        ver = embedding_version or self._embedding_version

        if user_text and user_text.strip():
            user_emb = self._client.embed_one(user_text.strip())
            rows.append(
                {
                    "conversation_id": session_id,
                    "turn_id": int(turn_id),
                    "speaker": "user",
                    "lang": lang,
                    "text": user_text.strip(),
                    "embedding": user_emb,
                    "embedding_version": ver,
                }
            )

        if assistant_text and assistant_text.strip():
            asst_emb = self._client.embed_one(assistant_text.strip())
            rows.append(
                {
                    "conversation_id": session_id,
                    "turn_id": int(turn_id),
                    "speaker": "assistant",
                    "lang": lang,
                    "text": assistant_text.strip(),
                    "embedding": asst_emb,
                    "embedding_version": ver,
                }
            )

        if rows:
            self._store.bulk_upsert(rows)

    # ----------------------------------------
    # KNN 検索（長期記憶の近傍抽出）
    # ----------------------------------------
    def knn_messages(
        self,
        session_id: str,
        query_text: str,
        k: int = 5,
        min_cosine: float = 0.2,
        role_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        最新ユーザ発話など query_text に近い会話履歴（長期記憶）を KNN 検索。
        返却: [{speaker, text, lang, turn_id, ts, score}] （score は cosine 類似度）
        """
        if not isinstance(query_text, str):
            query_text = str(query_text)
        query_emb = self._client.embed_one(query_text)
        return self._store.knn_messages(
            conversation_id=session_id,
            query_embedding=query_emb,
            k=k,
            min_cosine=min_cosine,
            role_filter=role_filter,
        )

    # ----------------------------------------
    # LLM へ渡すメモリ断片のフォーマット
    # ----------------------------------------
    @staticmethod
    def format_memory_snippets(excerpts: List[Dict[str, Any]], max_chars: int = 2400) -> str:
        """
        LLM テンプレートの "Memory" セクション用に、近傍会話抜粋を整形。
        - バイト数ではなく文字数基準で簡易的に切り詰め（サーバ側 Unicode 前提）
        """
        lines: List[str] = []
        for ex in excerpts:
            speaker = ex.get("speaker", "user")
            turn_id = ex.get("turn_id", "-")
            ts = ex.get("ts")
            text = ex.get("text", "")
            # テキストは 1 行に整形（改行はスペースへ）
            text_line = " ".join(str(text).split())
            lines.append(f"[{speaker}] turn:{turn_id} {ts}: {text_line}")
        joined = "\n".join(lines)
        if len(joined) > max_chars:
            joined = joined[: max_chars - 3] + "..."
        return joined

    # ----------------------------------------
    # ベクタストア用の埋め込み関数（RAG スクリプト互換）
    # ----------------------------------------
    def embedding_function_for_vectorstore(self) -> Callable[[List[str]], List[List[float]]]:
        """
        Chroma などの embedding_function に渡すためのコール可能を返す。
        """
        return _VectorStoreEmbeddingFn(self._client)
