# backend/worker/app/services/embeddings.py
# ------------------------------------------------------------
# 役割:
#  - Ollama の Embeddings API を叩いて mxbai-embed-large で埋め込み生成
#  - 一括/単発の埋め込みユーティリティ
#  - 保存ヘルパー（SQLAlchemy セッションに ConversationEmbedding を挿入）
# 環境変数:
#  - OLLAMA_HOST (例: http://ollama:11434)
#  - EMBEDDING_MODEL (デフォルト: mxbai-embed-large)
#  - EMBEDDING_DIM   (デフォルト: 1024)
# ------------------------------------------------------------
from __future__ import annotations

import os
import json
import time
from typing import List, Tuple, Optional

import requests
from sqlalchemy.orm import Session

from shared.app.models import ConversationEmbedding

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "mxbai-embed-large")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))


class EmbeddingClient:
    def __init__(self, host: str = OLLAMA_HOST, model: str = EMBEDDING_MODEL, timeout: float = 30.0):
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout

    def embed_texts(self, texts: List[str], retries: int = 2, backoff: float = 1.5) -> List[List[float]]:
        """
        複数テキストの埋め込みをまとめて生成する（Ollama は基本 1件ずつだがループで吸収）
        """
        vectors: List[List[float]] = []
        for t in texts:
            vec = self.embed_text(t, retries=retries, backoff=backoff)
            vectors.append(vec)
        return vectors

    def embed_text(self, text: str, retries: int = 2, backoff: float = 1.5) -> List[float]:
        """
        単一テキストの埋め込みを生成。失敗時はリトライ。
        """
        url = f"{self.host}/api/embeddings"
        payload = {"model": self.model, "prompt": text}
        for attempt in range(retries + 1):
            try:
                resp = requests.post(url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
                vec = data.get("embedding")
                if not isinstance(vec, list):
                    raise ValueError("Invalid embedding response format")
                # 明示的に float へ
                return [float(x) for x in vec]
            except Exception:
                if attempt >= retries:
                    raise
                time.sleep(backoff ** attempt)


def save_conversation_embeddings(
    db: Session,
    entries: List[Tuple[str, str, Optional[str], str, List[float], str]],
):
    """
    ConversationEmbedding を一括保存するヘルパー。
    entries: [(session_id, speaker, lang, text, vector, version), ...]
    """
    objs = []
    for session_id, speaker, lang, text, vec, version in entries:
        obj = ConversationEmbedding(
            session_id=session_id,
            speaker=speaker,
            lang=lang,
            text=text,
            embedding=vec,
            embedding_version=version,
        )
        objs.append(obj)
    db.add_all(objs)
    db.commit()
