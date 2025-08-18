from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

import requests

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ============================================================
# Public protocol (kept for type-compatibility across modules)
# ============================================================

@runtime_checkable
class EmbeddingClient(Protocol):
    """Minimal interface used across worker + scripts."""

    model_name: str
    dim: int

    def embed(self, text: str) -> List[float]:
        ...

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        ...

# ============================================================
# Utilities
# ============================================================

def _l2_norm(vec: Sequence[float]) -> float:
    return math.sqrt(sum(x * x for x in vec))

def _l2_normalize(vec: Sequence[float]) -> List[float]:
    n = _l2_norm(vec)
    if n == 0.0:
        return [0.0 for _ in vec]
    inv = 1.0 / n
    return [x * inv for x in vec]

def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"vector size mismatch: {len(a)} != {len(b)}")
    # Normalize both (robust to scale)
    an = _l2_normalize(a)
    bn = _l2_normalize(b)
    return float(sum(x * y for x, y in zip(an, bn)))

def cosine_similarities(vec: Sequence[float], mat: Sequence[Sequence[float]]) -> List[float]:
    return [cosine_similarity(vec, row) for row in mat]

# ============================================================
# Ollama embedding client
# ============================================================

@dataclass
class OllamaEmbeddingClient(EmbeddingClient):
    """
    Thin client for Ollama /api/embeddings.
    - OLLAMA_BASE_URL  e.g. http://ollama:11434  (default)
    - OLLAMA_EMBED_MODEL e.g. nomic-embed-text
    """
    model_name: str = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    base_url: str = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
    timeout_sec: float = float(os.getenv("OLLAMA_TIMEOUT_SEC", "60"))
    dim: int = int(os.getenv("OLLAMA_EMBED_DIM", "768"))  # safe default; will be overwritten if API returns size

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/api/embeddings"
        resp = requests.post(url, json=payload, timeout=self.timeout_sec)
        resp.raise_for_status()
        return resp.json()

    def embed(self, text: str) -> List[float]:
        # Ollama expects "prompt"
        body = {"model": self.model_name, "prompt": text}
        js = self._post(body)
        vec = js.get("embedding") or js.get("data") or js.get("vector")
        if vec is None:
            raise RuntimeError(f"Ollama embeddings: unexpected response keys: {list(js.keys())}")
        if isinstance(vec, dict) and "embedding" in vec:
            vec = vec["embedding"]
        if not isinstance(vec, list):
            raise RuntimeError(f"Embedding must be list, got {type(vec)}")
        # update dim if available
        self.dim = len(vec)
        return [float(x) for x in vec]

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        # There is no official batch API; iterate.
        return [self.embed(t) for t in texts]

# Backward-compat alias (if some code imported this name)
class OllamaEmbeddings(OllamaEmbeddingClient):
    pass

# ============================================================
# OpenAI embedding client (optional; only if OPENAI_API_KEY exists)
# ============================================================

@dataclass
class OpenAIEmbeddingClient(EmbeddingClient):
    """
    Minimal OpenAI embeddings wrapper.
    - OPENAI_API_KEY must be set
    - OPENAI_EMBED_MODEL (default: text-embedding-3-small)
    """
    model_name: str = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
    api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
    base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    timeout_sec: float = float(os.getenv("OPENAI_TIMEOUT_SEC", "60"))
    dim: int = int(os.getenv("OPENAI_EMBED_DIM", "1536"))

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def embed(self, text: str) -> List[float]:
        url = f"{self.base_url.rstrip('/')}/embeddings"
        payload = {"model": self.model_name, "input": text}
        resp = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout_sec)
        resp.raise_for_status()
        js = resp.json()
        data = js.get("data")
        if not data:
            raise RuntimeError(f"OpenAI embeddings: no data in response: {js}")
        vec = data[0]["embedding"]
        self.dim = len(vec)
        return [float(x) for x in vec]

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        # For simplicity and reliability, iterate (rate-limit friendly).
        return [self.embed(t) for t in texts]

# ============================================================
# Factory & high-level helpers (kept for compatibility)
# ============================================================

def _resolve_default_client() -> EmbeddingClient:
    # Prefer Ollama if reachable, else OpenAI if key exists
    prefer = os.getenv("EMBEDDINGS_BACKEND", "ollama").lower()
    if prefer == "openai":
        return OpenAIEmbeddingClient()
    return OllamaEmbeddingClient()

def embed_text(text: str, client: Optional[EmbeddingClient] = None) -> List[float]:
    client = client or _resolve_default_client()
    return client.embed(text)

def embed_text_batch(texts: Sequence[str], client: Optional[EmbeddingClient] = None) -> List[List[float]]:
    client = client or _resolve_default_client()
    return client.embed_texts(texts)

# ============================================================
# Persistence (best-effort; won't break import if DB not ready)
# ============================================================

def save_conversation_embeddings(
    session_id: str,
    messages: List[Dict[str, Any]],
    client: Optional[EmbeddingClient] = None,
) -> None:
    """
    Best-effort persisting of conversation embeddings.
    - If DB layer / models are not available at import time, we only log (do not raise).
    - If available, we insert (or upsert) per message.
    """
    client = client or _resolve_default_client()
    try:
        # Lazy imports (avoid import-time coupling)
        from shared.app import database as _db
        from shared.app import models as _models

        engine = _db.get_engine()
        if not engine:
            logger.warning("No DB engine available; skip saving conversation embeddings.")
            return

        # Prepare rows
        texts: List[str] = []
        metas: List[Tuple[int, Dict[str, Any]]] = []  # (index, original message)
        for i, m in enumerate(messages):
            content = (m.get("content") or "").strip()
            if content:
                texts.append(content)
                metas.append((i, m))

        if not texts:
            logger.info("No text messages to embed.")
            return

        vectors = embed_text_batch(texts, client=client)

        with engine.begin() as conn:
            for (i, m), vec in zip(metas, vectors):
                row = _models.ConversationEmbedding(
                    session_id=session_id,
                    turn_index=i,
                    role=m.get("role") or "assistant",
                    text=m.get("content") or "",
                    embedding=json.dumps(vec),
                    model_name=getattr(client, "model_name", "unknown"),
                )
                conn.execute(_models.conversation_embeddings.insert().values(**row.__dict__))  # type: ignore[attr-defined]

        logger.info("Saved %d conversation embeddings (session=%s).", len(vectors), session_id)

    except Exception as e:
        # Do not break worker startup on optional feature
        logger.warning("save_conversation_embeddings skipped (reason=%s)", e, exc_info=False)

# ============================================================
# Optional: HNSW builder (kept for compatibility if referenced)
# ============================================================

def build_hnsw_index(vectors: Sequence[Sequence[float]]) -> Any:
    """
    Provide a small hook if callers expect 'build_hnsw_index' to exist.
    To keep dependencies light, return a naive structure (list) by default.
    Replace with hnswlib integration if/when needed.
    """
    return [list(map(float, v)) for v in vectors]

# Module-level re-exports for backward naming compatibility
# (some older modules may have imported these names)
OllamaEmbeddingsClient = OllamaEmbeddingClient  # common typo guard
