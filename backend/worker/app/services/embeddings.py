# -*- coding: utf-8 -*-
"""
Ollama の Embeddings API クライアント（堅牢版）
- mxbai-embed-large を前提に /api/embeddings を 1テキストずつ呼び出す
- 失敗/空ベクトル/型不正をスキップ
- 軽いリトライとタイムアウト
"""

from __future__ import annotations

import os
import time
import json
import logging
from typing import List, Optional
import requests

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DEFAULT_BASE_URL = os.getenv("OLLAMA_HOST", "http://ollama:11434")
DEFAULT_MODEL = os.getenv("EMBED_MODEL", "mxbai-embed-large")
REQ_TIMEOUT = float(os.getenv("EMBED_REQ_TIMEOUT", "30"))  # 秒
RETRY_TIMES = int(os.getenv("EMBED_REQ_RETRIES", "2"))
RETRY_WAIT = float(os.getenv("EMBED_REQ_RETRY_WAIT", "1.0"))  # 秒


class OllamaEmbeddingClient:
    """
    Ollama /api/embeddings を叩く薄いクライアント。
    - 1テキストずつ送る（Ollama は配列一括に対応していないため）
    - レスポンス例: {"embedding":[...]} を想定
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.endpoint = f"{self.base_url}/api/embeddings"

    def _embed_one(self, text: str) -> Optional[List[float]]:
        """
        単一テキストを埋め込み。失敗時は None。
        """
        if not isinstance(text, str) or not text.strip():
            return None

        payload = {
            "model": self.model,
            "prompt": text,
        }

        # 軽いリトライ
        for attempt in range(RETRY_TIMES + 1):
            try:
                resp = requests.post(self.endpoint, json=payload, timeout=REQ_TIMEOUT)
                if resp.status_code != 200:
                    logger.warning("embeddings http %s: %s", resp.status_code, resp.text[:200])
                    raise RuntimeError(f"HTTP {resp.status_code}")
                data = resp.json()
                # 期待形: {"embedding":[float,...]}
                emb = data.get("embedding")
                if not isinstance(emb, list) or len(emb) == 0:
                    logger.warning("empty or invalid embedding: %s", json.dumps(data)[:200])
                    return None
                # 数値化を強制
                try:
                    emb = [float(x) for x in emb]
                except Exception:
                    logger.warning("non-float element found in embedding")
                    return None
                return emb
            except Exception as e:
                if attempt < RETRY_TIMES:
                    time.sleep(RETRY_WAIT)
                    continue
                logger.error("embed request failed (final): %s", e)
                return None

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """
        複数テキストを順次埋め込み。None は除外して返す（順序は維持できない点に注意）。
        01_build_knowledge_graph.py 側で docs とベクトル件数整合は再調整済み。
        """
        vectors: List[List[float]] = []
        for idx, t in enumerate(texts):
            v = self._embed_one(t)
            if v is None:
                # None はスキップ（呼び出し側で件数整合処理）
                continue
            vectors.append(v)
        return vectors
