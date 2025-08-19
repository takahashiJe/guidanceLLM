# backend/worker/app/services/embeddings.py
# -*- coding: utf-8 -*-
"""
Embeddings（会話の長期記憶/RAG埋め込み）サービスの本実装。

【設計方針】
- アプリケーション全体で利用する唯一の公開クラスとして `EmbeddingService` を提供する。
- `EmbeddingService` は、RAG知識ベース構築と会話の長期記憶管理の両方に必要なインターフェースをすべて備える。
- 内部実装として、Ollamaクライアント、DB層(pgvector)を責務分離されたプライベートクラスとして維持する。
- 堅牢性（リトライ、タイムアウト）、効率性（LRUキャッシュ、L2正規化）を担保する。
"""

from __future__ import annotations

import os
import math
import time
import json
import logging
import hashlib
import functools
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

# pgvector の SQLAlchemy 型
try:
    from pgvector.sqlalchemy import Vector
except ImportError:
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
# 内部実装: ユーティリティ
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
# 内部実装: Ollama Embeddings クライアント
# ============================================

class _OllamaEmbeddingsClient:
    """
    Ollama の /api/embeddings を叩くシンプルなクライアント。（内部利用）
    - リトライ（指数バックオフ＋ジッター）、タイムアウト、応答検証、L2 正規化を責務に持つ。
    """
    def __init__(
        self,
        host: str,
        model: str,
        timeout: float,
        max_retries: int,
        embedding_dim: int,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.embedding_dim = embedding_dim
        self._session = requests.Session()
        self._url = f"{self.host}/api/embeddings"

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
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
        raise RuntimeError(f"Ollama embeddings request failed after retries: {last_exc}")

    def _post_and_extract(self, text: str) -> List[float]:
        payload = {"model": self.model, "prompt": text}
        data = self._post(payload)
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
        emb = self._post_and_extract(text)
        emb = _l2_normalize(emb)
        return key, emb

    def embed_one(self, text: str) -> List[float]:
        key = _sha_key(text)
        _, emb = self._embed_one_cached(key, text)
        return emb

    def embed_many(self, texts: Iterable[str]) -> List[List[float]]:
        return [self.embed_one(t) for t in texts]


# ============================================
# 内部実装: DB 層 (pgvector)
# ============================================

class _ConversationMemoryStore:
    """
    会話の長期記憶（conversation_message_embeddings テーブル）を司る DB 層。（内部利用）
    """
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def bulk_upsert(self, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        with self._session_factory() as db:
            try:
                from sqlalchemy.dialects.postgresql import insert
                table = models.ConversationMessageEmbedding.__table__
                stmt = insert(table).values(rows)
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
        k: int,
        min_cosine: float,
        role_filter: Optional[str],
    ) -> List[Dict[str, Any]]:
        if Vector is None:
            raise RuntimeError("pgvector 'Vector' type is not available. Please install pgvector.")

        max_distance = 1.0 - float(min_cosine)
        max_distance = max(0.0, min(2.0, max_distance))

        base_sql = """
            SELECT speaker, lang, text, turn_id, ts, (embedding <=> :query_vec) AS distance
            FROM conversation_message_embeddings
            WHERE conversation_id = :cid AND (embedding <=> :query_vec) <= :max_dist
        """
        if role_filter:
            base_sql += " AND speaker = :role "
        base_sql += " ORDER BY (embedding <=> :query_vec) ASC LIMIT :k "

        with self._session_factory() as db:
            vec_type = Vector(EMBEDDING_DIM)
            params: Dict[str, Any] = {
                "cid": conversation_id, "query_vec": query_embedding,
                "max_dist": max_distance, "k": int(k),
            }
            if role_filter:
                params["role"] = role_filter

            stmt = text(base_sql).bindparams(query_vec=vec_type)
            rows = db.execute(stmt, params).mappings().all()

        return [
            {
                "speaker": r["speaker"], "lang": r["lang"], "text": r["text"],
                "turn_id": int(r["turn_id"]), "ts": r["ts"],
                "score": 1.0 - float(r["distance"]),
            }
            for r in rows
        ]


# ============================================
# << 公開クラス >> : EmbeddingService
# ============================================

class EmbeddingService:
    """
    埋め込みベクトルに関する全機能を提供する唯一の公開クラス（Facade）。
    - RAG用の知識ベクトル化と、会話の長期記憶管理の両方を担当する。
    - 既存の全インターフェース（`Embeddings`クラスと旧`EmbeddingsService`のメソッド）を実装する。
    """
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        ollama_host: str = OLLAMA_HOST,
        model: str = EMBEDDING_MODEL,
        embedding_version: str = EMBEDDING_VERSION,
        embedding_dim: int = EMBEDDING_DIM,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self.embedding_dim = embedding_dim
        self._embedding_version = embedding_version
        self._client = _OllamaEmbeddingsClient(
            host=ollama_host,
            model=model,
            embedding_dim=embedding_dim,
            timeout=timeout,
            max_retries=max_retries,
        )
        self._store = _ConversationMemoryStore(session_factory=session_factory)

    # --- 1. RAG/汎用テキスト埋め込みAPI ---

    def embed_text(self, text: str) -> List[float]:
        """単一テキストをベクトル化（L2正規化済み）。"""
        if not isinstance(text, str):
            text = str(text or "")
        return self._client.embed_one(text.strip())

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        """
        複数テキストをベクトル化（L2正規化済み）。
        - 空文字列やNoneはゼロベクトルで埋め、入力と同じ長さを維持する。
        """
        if not texts:
            return []
        
        results: List[List[float]] = []
        zero_vec = [0.0] * self.embedding_dim
        for t in texts:
            s = (t or "").strip()
            if not s:
                results.append(list(zero_vec))
                continue
            try:
                vec = self.embed_text(s)
                # 念のため次元を合わせる
                if len(vec) != self.embedding_dim:
                    vec = (vec + zero_vec)[:self.embedding_dim]
                results.append(vec)
            except Exception as e:
                logger.warning(f"Failed to embed text chunk: {s[:80]}... Error: {e}")
                results.append(list(zero_vec))
        return results

    # --- 2. 後方互換/エイリアスメソッド ---

    def embed_query(self, text: str) -> List[float]:
        """`embed_text`の別名。検索クエリ用。"""
        return self.embed_text(text)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """`embed_texts`の別名。文書群用。"""
        return self.embed_texts(texts)

    def __call__(self, texts: List[str]) -> List[List[float]]:
        """ChromaDBの`embedding_function`として利用可能にするためのcallable実装。"""
        if not isinstance(texts, list):
            raise TypeError("EmbeddingService.__call__ expects List[str].")
        return self.embed_texts(texts)
    
    def embedding_function_for_vectorstore(self) -> Callable[[List[str]], List[List[float]]]:
        """ChromaDBなどに渡すためのコール可能オブジェクトを返す。"""
        return self

    # --- 3. 会話の長期記憶API ---

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
        会話の1往復（ユーザー発話/アシスタント応答）をベクトル化してDBに保存する。
        """
        rows: List[Dict[str, Any]] = []
        ver = embedding_version or self._embedding_version

        texts_to_embed = []
        if user_text and user_text.strip():
            texts_to_embed.append(("user", user_text.strip()))
        if assistant_text and assistant_text.strip():
            texts_to_embed.append(("assistant", assistant_text.strip()))
            
        if not texts_to_embed:
            return

        speakers = [item[0] for item in texts_to_embed]
        stripped_texts = [item[1] for item in texts_to_embed]
        embeddings = self.embed_texts(stripped_texts)

        for speaker, text, emb in zip(speakers, stripped_texts, embeddings):
            rows.append({
                "conversation_id": session_id,
                "turn_id": int(turn_id),
                "speaker": speaker,
                "lang": lang,
                "text": text,
                "embedding": emb,
                "embedding_version": ver,
                "ts": datetime.utcnow(),
            })

        if rows:
            self._store.bulk_upsert(rows)

    def upsert_message(
        self,
        session_id: str, # NOTE: conversation_id is the primary key for memory
        conversation_id: str,
        turn_id: int,
        speaker: str,
        lang: str,
        text: str,
        ts: datetime,
        embedding_version: Optional[str] = None,
        db: Optional[Session] = None, # `state.py`からの呼び出しを考慮
    ) -> None:
        """
        単一のメッセージをベクトル化してDBに保存する（state.py互換）。
        """
        if not text or not text.strip():
            return
            
        stripped_text = text.strip()
        embedding = self.embed_text(stripped_text)
        ver = embedding_version or self._embedding_version
        
        row = {
            "conversation_id": conversation_id,
            "turn_id": int(turn_id),
            "speaker": speaker,
            "lang": lang,
            "text": stripped_text,
            "embedding": embedding,
            "embedding_version": ver,
            "ts": ts,
        }
        # state.pyのように外部セッションを渡された場合でも動作するように考慮
        if db:
            # 1行だけなので、既存のbulk_upsertを流用
            temp_store = _ConversationMemoryStore(lambda: db)
            temp_store.bulk_upsert([row])
        else:
            self._store.bulk_upsert([row])

    def knn_messages(
        self,
        session_id: str,
        query_text: str,
        k: int = 5,
        min_cosine: float = 0.2,
        role_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        クエリテキストに意味的に近い過去の会話履歴をKNN検索する。
        """
        query_emb = self.embed_text(query_text)
        return self._store.knn_messages(
            conversation_id=session_id,
            query_embedding=query_emb,
            k=k,
            min_cosine=min_cosine,
            role_filter=role_filter,
        )

    # --- 4. 補助的なユーティリティ ---

    @staticmethod
    def format_memory_snippets(excerpts: List[Dict[str, Any]], max_chars: int = 2400) -> str:
        """
        KNN検索結果をLLMのプロンプトに埋め込むための整形済み文字列を生成する。
        """
        lines: List[str] = []
        for ex in excerpts:
            speaker = ex.get("speaker", "user")
            turn_id = ex.get("turn_id", "-")
            ts_val = ex.get("ts")
            ts_str = ts_val.isoformat() if isinstance(ts_val, datetime) else str(ts_val or "")
            text_line = " ".join(str(ex.get("text", "")).split())
            lines.append(f"[{speaker}] turn:{turn_id} {ts_str}: {text_line}")
        joined = "\n".join(lines)
        return (joined[: max_chars - 3] + "...") if len(joined) > max_chars else joined