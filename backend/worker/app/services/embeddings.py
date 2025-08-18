# -*- coding: utf-8 -*-
"""
埋め込み（Embeddings）サービス
------------------------------------------------------------
役割:
  - Ollama の mxbai-embed-large を用いてテキストを埋め込みベクトル化
  - conversation_embeddings テーブルへ保存・再インデックス
  - セッション内の k 近傍検索（pgvector なしでも Python 側でコサイン類似度計算）

特長:
  - HTTP と Python SDK（ollama）両対応。HTTP を優先し、SDK をフォールバックに利用。
  - pgvector がない環境でも安全に動作（将来の pgvector 導入に備えて拡張ポイントを用意）
  - 既存コードからの呼び出しに配慮し、クラス API と関数 API の両方を提供

環境変数:
  - OLLAMA_HOST               : Ollama のホストURL（例: http://ollama:11434）
  - EMBEDDING_MODEL           : 既定 "mxbai-embed-large"
  - EMBEDDING_VERSION         : 既定 "mxbai-embed-large@v1"（再インデックス判定に使用）
  - EMBEDDING_HTTP_TIMEOUT    : 既定 60（秒）
  - EMBEDDING_CANDIDATE_LIMIT : 既定 200（kNN候補として DB から読む最大件数）
  - EMBEDDING_MAX_TEXT_LEN    : 既定 4096（超過時は安全に切り詰め）

前提:
  - shared.app.models に ConversationEmbedding モデルが定義済み
    （columns: id, session_id, speaker, lang, ts, text, embedding_version, embedding[float] など）
"""

from __future__ import annotations

import os
import math
import time
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from shared.app.database import SessionLocal
from shared.app import models

logger = logging.getLogger(__name__)


# ============================================================
# 内部: Ollama クライアント（HTTP優先）
# ============================================================
class OllamaEmbeddingClient:
    """Ollama の embeddings API を叩く軽量クライアント。HTTP を優先し、SDK をフォールバック。"""

    def __init__(
        self,
        model: Optional[str] = None,
        host: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> None:
        self.model = model or os.getenv("EMBEDDING_MODEL", "mxbai-embed-large")
        self.host = (host or os.getenv("OLLAMA_HOST") or "http://ollama:11434").rstrip("/")
        self.timeout = int(os.getenv("EMBEDDING_HTTP_TIMEOUT", str(timeout or 60)))

        # HTTP エンドポイント（Ollama 0.1 系: /api/embeddings, 0.2 以降: /api/embed）
        self._endpoints = ["/api/embeddings", "/api/embed"]

    def embed(self, text: str) -> List[float]:
        """単一テキストの埋め込みを取得。HTTP -> SDK の順でトライ。"""
        text = self._sanitize_text(text)
        # 1) HTTP
        for ep in self._endpoints:
            try:
                url = f"{self.host}{ep}"
                payload = self._make_http_payload(text)
                res = requests.post(url, json=payload, timeout=self.timeout)
                res.raise_for_status()
                vec = self._parse_http_embeddings(res.json())
                if vec:
                    return list(map(float, vec))
            except Exception as e:
                logger.debug(f"Ollama HTTP embeddings failed at {ep}: {e}")

        # 2) SDK フォールバック
        try:
            import ollama  # type: ignore
            # SDK の仕様差吸収（旧: prompt 新: input）
            try:
                data = ollama.embeddings(model=self.model, prompt=text)  # 旧
            except TypeError:
                data = ollama.embeddings(model=self.model, input=text)   # 新
            vec = data.get("embedding") or (data.get("data") and data["data"][0].get("embedding"))
            if not vec:
                raise RuntimeError("SDK embeddings result is empty.")
            return list(map(float, vec))
        except Exception as e:
            raise RuntimeError(f"Ollama embeddings failed (HTTP & SDK): {e}")

    # 将来的な一括APIに備えて用意（現状は逐次）
    def embed_many(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]

    # ------------------------ 内部ユーティリティ ------------------------
    def _make_http_payload(self, text: str) -> Dict[str, Any]:
        # /api/embeddings（prompt）と /api/embed（input）の両対応
        return {"model": self.model, "prompt": text, "input": text}

    def _parse_http_embeddings(self, data: Dict[str, Any]) -> Optional[List[float]]:
        # /api/embeddings: {"embedding":[...]}
        if "embedding" in data and isinstance(data["embedding"], list):
            return data["embedding"]
        # /api/embed: {"data":[{"embedding":[...]}]}
        if "data" in data and isinstance(data["data"], list) and data["data"]:
            first = data["data"][0]
            emb = first.get("embedding")
            if isinstance(emb, list):
                return emb
        return None

    def _sanitize_text(self, text: str) -> str:
        max_len = int(os.getenv("EMBEDDING_MAX_TEXT_LEN", "4096"))
        s = (text or "").strip()
        if len(s) > max_len:
            s = s[:max_len]
        return s


# ============================================================
# 数学ユーティリティ（pgvector ない場合のコサイン類似度）
# ============================================================
def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ============================================================
# 埋め込みサービス（保存・検索・再インデックス）
# ============================================================
class EmbeddingService:
    """
    会話の長期記憶（conversation_embeddings）の保存・検索。
    - upsert_message: 1件保存（バージョン付き）
    - search_similar: セッション内 kNN 検索（pgvector 無でも動作）
    - batch_reindex_session: 旧バージョン/欠損の再インデックス
    - health_check: 埋め込み系の疎通
    """

    def __init__(
        self,
        client: Optional[OllamaEmbeddingClient] = None,
        embedding_version: Optional[str] = None,
        candidate_limit: Optional[int] = None,
    ) -> None:
        self.client = client or OllamaEmbeddingClient()
        self.embedding_version = embedding_version or os.getenv("EMBEDDING_VERSION", "mxbai-embed-large@v1")
        self.candidate_limit = int(os.getenv("EMBEDDING_CANDIDATE_LIMIT", str(candidate_limit or 200)))

    # ----------------------------- 保存系 -----------------------------
    def upsert_message(
        self,
        *,
        session_id: str,
        speaker: str,          # "user" / "assistant" / "system"
        lang: str,             # "ja" / "en" / "zh"
        text: str,
        conversation_id: Optional[str] = None,
        ts: Optional[datetime] = None,
        db: Optional[Session] = None,
    ) -> int:
        """
        単一メッセージを埋め込み保存。戻り値は保存したレコードの ID。
        """
        vector = self.client.embed(text)
        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True
        try:
            row = models.ConversationEmbedding(
                session_id=session_id,
                conversation_id=conversation_id,
                speaker=speaker,
                lang=lang,
                ts=ts or datetime.utcnow(),
                text=text,
                embedding_version=self.embedding_version,
                embedding=vector,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return int(row.id)
        finally:
            if close_db:
                db.close()

    # ----------------------------- 検索系 -----------------------------
    def search_similar(
        self,
        *,
        session_id: str,
        query_text: str,
        top_k: int = 5,
        min_cosine: float = 0.0,
        db: Optional[Session] = None,
    ) -> List[Dict[str, Any]]:
        """
        セッション内の会話から k 近傍検索。
        - 既定は直近 self.candidate_limit 件をロードして Python 側でコサイン類似度を計算
        - 戻り値はスコア降順の辞書リスト
        """
        qvec = self.client.embed(query_text)

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True
        try:
            stmt = (
                select(models.ConversationEmbedding)
                .where(
                    models.ConversationEmbedding.session_id == session_id,
                    models.ConversationEmbedding.embedding_version == self.embedding_version,
                )
                .order_by(desc(models.ConversationEmbedding.ts))
                .limit(self.candidate_limit)
            )
            rows = db.execute(stmt).scalars().all()

            # 類似度算出（pgvector 無環境でも確実に動く）
            scored: List[Tuple[float, models.ConversationEmbedding]] = []
            for r in rows:
                if not r.embedding:
                    continue
                score = _cosine_similarity(qvec, r.embedding)
                if score >= min_cosine:
                    scored.append((score, r))

            scored.sort(key=lambda x: x[0], reverse=True)
            top = scored[: max(1, top_k)]

            results: List[Dict[str, Any]] = []
            for score, r in top:
                results.append(
                    {
                        "id": int(r.id),
                        "session_id": r.session_id,
                        "conversation_id": getattr(r, "conversation_id", None),
                        "speaker": r.speaker,
                        "lang": r.lang,
                        "ts": r.ts.isoformat() if r.ts else None,
                        "text": r.text,
                        "embedding_version": r.embedding_version,
                        "cosine": float(score),
                    }
                )
            return results
        finally:
            if close_db:
                db.close()

    # ------------------------- 再インデックス系 -------------------------
    def batch_reindex_session(
        self,
        *,
        session_id: str,
        force: bool = False,
        db: Optional[Session] = None,
    ) -> int:
        """
        セッション内で、埋め込み未生成/旧バージョンの行を再インデックス。
        - force=True で全件再インデックス
        戻り値: 更新件数
        """
        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        updated = 0
        try:
            # 対象行を取得
            if force:
                stmt = (
                    select(models.ConversationEmbedding)
                    .where(models.ConversationEmbedding.session_id == session_id)
                    .order_by(desc(models.ConversationEmbedding.ts))
                )
            else:
                stmt = (
                    select(models.ConversationEmbedding)
                    .where(
                        models.ConversationEmbedding.session_id == session_id,
                        (
                            (models.ConversationEmbedding.embedding == None)  # noqa: E711
                            | (models.ConversationEmbedding.embedding_version != self.embedding_version)
                        ),
                    )
                    .order_by(desc(models.ConversationEmbedding.ts))
                )

            rows = db.execute(stmt).scalars().all()
            if not rows:
                return 0

            for r in rows:
                try:
                    r.embedding = self.client.embed(r.text or "")
                    r.embedding_version = self.embedding_version
                    updated += 1
                except Exception as e:
                    logger.warning(f"reindex failed (id={r.id}): {e}")
            db.commit()
            return updated
        finally:
            if close_db:
                db.close()

    # --------------------------- ヘルスチェック ---------------------------
    def health_check(self) -> Dict[str, Any]:
        """
        埋め込みエンドポイントの疎通を確認（短いテキストで1回だけ呼ぶ）。
        """
        t0 = time.time()
        try:
            v = self.client.embed("hello")
            ok = isinstance(v, list) and len(v) > 0
            return {
                "ok": ok,
                "latency_ms": int((time.time() - t0) * 1000),
                "model": self.client.model,
                "host": self.client.host,
                "dim": (len(v) if ok else 0),
            }
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "latency_ms": int((time.time() - t0) * 1000),
                "model": self.client.model,
                "host": self.client.host,
            }


# ============================================================
# 互換性のためのモジュールレベル関数（既存呼び出しに配慮）
# ============================================================
_default_service: Optional[EmbeddingService] = None


def _svc() -> EmbeddingService:
    global _default_service
    if _default_service is None:
        _default_service = EmbeddingService()
    return _default_service


def embed_text(text: str) -> List[float]:
    """テキストを埋め込み（互換API）。"""
    return _svc().client.embed(text)


def upsert_conversation_message(
    *,
    session_id: str,
    speaker: str,
    lang: str,
    text: str,
    conversation_id: Optional[str] = None,
    ts: Optional[datetime] = None,
) -> int:
    """会話メッセージを保存（互換API）。"""
    return _svc().upsert_message(
        session_id=session_id,
        speaker=speaker,
        lang=lang,
        text=text,
        conversation_id=conversation_id,
        ts=ts,
    )


def knn_search(
    *,
    session_id: str,
    query_text: str,
    top_k: int = 5,
    min_cosine: float = 0.0,
) -> List[Dict[str, Any]]:
    """k 近傍検索（互換API）。"""
    return _svc().search_similar(
        session_id=session_id,
        query_text=query_text,
        top_k=top_k,
        min_cosine=min_cosine,
    )


def reindex_session(session_id: str, force: bool = False) -> int:
    """再インデックス（互換API）。"""
    return _svc().batch_reindex_session(session_id=session_id, force=force)


def embeddings_health() -> Dict[str, Any]:
    """疎通確認（互換API）。"""
    return _svc().health_check()

# -------------- Embeddings ファサード（RAGスクリプト互換用） --------------
# 目的:
# - backend/scripts/01_build_knowledge_graph.py 等の既存スクリプトが想定する
#   `Embeddings` クラスを提供し、内部では ConversationEmbedder を用いて
#   mxbai-embed-large による埋め込みを実行する。
# - 会話用実装（ConversationEmbedder）をそのまま再利用しつつ、RAG の
#   バッチ埋め込み（embed_texts）インターフェースを提供する。

class Embeddings:
    """
    RAG 用の薄い互換ラッパ。
    - 既存スクリプトが想定する `embed_texts(List[str]) -> List[List[float]]`
      を提供する。
    - 内部では ConversationEmbedder を使用して単文を逐次埋め込み（CPU）。
    - モデル名・次元数の参照も提供する。
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        device: str = "cpu",
        default_lang: Optional[str] = None,
        chunk_size: int = 128,
    ) -> None:
        # ConversationEmbedder は同一モジュール内の実装を想定
        # （なければ from worker.app.services.embeddings import ConversationEmbedder で import する）
        self._inner = ConversationEmbedder(
            model_name=model_name,
            device=device,
            default_lang=default_lang,
        )
        # 大量ドキュメント時のメモリ圧迫回避のための逐次処理サイズ
        self._chunk_size = max(1, int(chunk_size))

    @property
    def model_name(self) -> str:
        """利用中の埋め込みモデル名を返す。"""
        # ConversationEmbedder 側に model_name 属性がある前提。なければ既定名を返す。
        return getattr(self._inner, "model_name", "mxbai-embed-large")

    @property
    def dim(self) -> int:
        """埋め込みベクトルの次元数を返す（mxbai-embed-large は 1024）。"""
        return int(getattr(self._inner, "dim", 1024))

    def embed_text(self, text: str, lang: Optional[str] = None) -> List[float]:
        """
        単一テキストの埋め込み（ヘルパー）。RAG 側で単発呼び出しが必要な場合に備える。
        """
        if text is None:
            raise ValueError("text は None にできません。")
        t = text.strip()
        if not t:
            # 空文字は全ゼロベクトルを返して呼び出し側でスキップしやすくする
            return [0.0] * self.dim
        return self._inner.embed_text(t, lang=lang)

    def embed_texts(self, texts: List[str], lang: Optional[str] = None) -> List[List[float]]:
        """
        複数テキストをバッチ埋め込み。
        - 内部ではチャンクに分けて逐次 embed してメモリ使用量を抑制。
        - 入力が None/空文字の場合はゼロベクトルを返す（スキップしやすくするための方針）。
        """
        if texts is None:
            raise ValueError("texts は None にできません。")

        results: List[List[float]] = []
        dim = self.dim

        def _embed_one(s: Optional[str]) -> List[float]:
            if s is None:
                return [0.0] * dim
            t = s.strip()
            if not t:
                return [0.0] * dim
            return self._inner.embed_text(t, lang=lang)

        # チャンク逐次処理
        n = len(texts)
        if n == 0:
            return results

        for i in range(0, n, self._chunk_size):
            chunk = texts[i : i + self._chunk_size]
            for s in chunk:
                results.append(_embed_one(s))

        return results
