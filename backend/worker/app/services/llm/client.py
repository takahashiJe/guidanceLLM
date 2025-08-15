# -*- coding: utf-8 -*-
"""
Ollama API クライアント
- qwen3:30b を既定で使用
- テキスト生成（自然文）
- JSON生成（構造化出力）: format="json" を利用し、厳格パース
- リトライ/タイムアウト/簡易バックオフ
"""

import json
import os
import time
from typing import Any, Dict, Optional

import requests


class OllamaClient:
    """
    Ollama の /api/generate を使用するシンプルなクライアント。
    - モデルは環境変数で切替可能（既定: qwen3:30b）
    - format="json" を指定すると厳密なJSON文字列が返る
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_sec: int = 60,
        max_retries: int = 2,
        retry_backoff_sec: float = 1.5,
    ) -> None:
        self.base_url = base_url or os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen3:30b")
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self.retry_backoff_sec = retry_backoff_sec

        self._endpoint = f"{self.base_url.rstrip('/')}/api/generate"

    def _post_generate(self, payload: Dict[str, Any]) -> str:
        """/api/generate を叩いて response['response'] を返す（ストリームOFF）"""
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.post(
                    self._endpoint,
                    json=payload,
                    timeout=self.timeout_sec,
                )
                resp.raise_for_status()
                data = resp.json()
                # Ollamaのgenerateは streaming=false でも 'response' に本文が入る
                text = data.get("response", "")
                if not isinstance(text, str):
                    text = str(text)
                return text.strip()
            except Exception as e:
                last_exc = e
                time.sleep(self.retry_backoff_sec * (attempt + 1))
        # リトライ尽きた場合
        raise RuntimeError(f"Ollama generate failed: {last_exc}")

    # ---- 自然文生成 ---------------------------------------------------------
    def invoke_completion(
        self,
        prompt: str,
        temperature: float = 0.4,
        top_p: float = 0.9,
        seed: Optional[int] = 7,
    ) -> str:
        """
        自然文の単発生成。
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
            },
        }
        if seed is not None:
            payload["options"]["seed"] = seed
        return self._post_generate(payload)

    # ---- JSON構造化生成 -----------------------------------------------------
    def invoke_structured_completion(
        self,
        prompt: str,
        temperature: float = 0.0,
        top_p: float = 1.0,
        seed: Optional[int] = 7,
    ) -> Dict[str, Any]:
        """
        JSONモードでの応答を辞書で返す。
        - LLM側で厳密なJSONのみ出力させるプロンプトを使用する前提
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",  # 重要：厳密JSONを返す
            "options": {
                "temperature": temperature,
                "top_p": top_p,
            },
        }
        if seed is not None:
            payload["options"]["seed"] = seed

        text = self._post_generate(payload)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            # 乱れた出力の際は整形トライ（よくある末尾カンマ/コードブロック対策）
            repaired = text.strip().strip("`").strip()
            try:
                return json.loads(repaired)
            except Exception:
                raise RuntimeError(f"Invalid JSON from model: {text[:300]} ... ({e})")
