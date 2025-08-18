# worker/app/services/embeddings.py
from __future__ import annotations

import os
import math
import json
from typing import Iterable, List, Optional, Sequence, Tuple, Union, Any

import requests
from sqlalchemy import MetaData, Table, insert
from sqlalchemy.orm import Session

from shared.app.database import SessionLocal
from shared.app import models  # ORM が無い列はリフレクションで対応する


# ===============================
# Embedding client interface
# ===============================

class EmbeddingClient:
    """埋め込みクライアントのインターフェース"""
    def embed(self, text: str) -> List[float]:
        raise NotImplementedError

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        # デフォルト実装：逐次 embed
        return [self.embed(t) for t in texts]


# ===============================
# Ollama implementation
# ===============================

class OllamaEmbeddingClient(EmbeddingClient):
    """
    Ollama の /api/embeddings を使って埋め込みを取得するクライアント。
    - 環境変数:
      OLLAMA_BASE_URL (例: http://ollama:11434)
      OLLAMA_EMBED_MODEL (例: mxbai-embed-large / nomic-embed-text 等)
    """
    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL") or "http://ollama:11434").rstrip("/")
        self.model = model or os.getenv("OLLAMA_EMBED_MODEL") or "nomic-embed-text"
        self.timeout = timeout

        # tags を叩いて疎通・モデル存在を早期検知（失敗しても遅延検知に任せる）
        try:
            requests.get(f"{self.base_url}/api/tags", timeout=5)
        except Exception:
            # ログ基盤があれば warning
            pass

    def _post_embeddings(self, payload: dict) -> dict:
        url = f"{self.base_url}/api/embeddings"
        r = requests.post(url, json=payload, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def embed(self, text: str) -> List[float]:
        if not isinstance(text, str):
            text = "" if text is None else str(text)
        payload = {"model": self.model, "input": text}
        data = self._post_embeddings(payload)
        # Ollama は {"embedding":[...]} を返す
        emb = data.get("embedding")
        if isinstance(emb, list):
            return [float(x) for x in emb]
        # 念のため複数形式にも対応
        if isinstance(emb, (tuple,)):
            return [float(x) for x in list(emb)]
        raise ValueError("Unexpected embeddings response format from Ollama")

    def embed_texts(self, texts: Sequence[str]) -> List[List[float]]:
        # 現状 Ollama は単発入力想定なので逐次投げる（将来一括 API が安定したら置換可）
        out: List[List[float]] = []
        for t in texts:
            out.append(self.embed(t))
        return out


# ===============================
# Factory
# ===============================

def create_embedding_client() -> EmbeddingClient:
    """
    既定は Ollama。将来 OpenAI などを増やす場合は ENV で切り替え。
    """
    provider = (os.getenv("EMBEDDING_BACKEND") or os.getenv("EMBEDDINGS_BACKEND") or "ollama").lower()
    if provider in ("ollama", "local"):
        return OllamaEmbeddingClient()
    # 追加プロバイダはここに分岐を増やす
    return OllamaEmbeddingClient()


# ===============================
# Utilities
# ===============================

def _norm(v: Sequence[float]) -> float:
    return math.sqrt(sum(float(x) * float(x) for x in v)) or 1.0

def _cosine(u: Sequence[float], v: Sequence[float]) -> float:
    # 長さが違う場合は短い方に合わせる
    n = min(len(u), len(v))
    if n == 0:
        return 0.0
    dot = sum(float(u[i]) * float(v[i]) for i in range(n))
    return dot / (_norm(u[:n]) * _norm(v[:n]))

def cosine_similarities(
    query_vector: Sequence[float],
    matrix_vectors: Sequence[Sequence[float]],
) -> List[float]:
    """query_vector と各ベクトルのコサイン類似度を返す"""
    return [_cosine(query_vector, vec) for vec in matrix_vectors]


# 使い勝手のためのショートカット関数（他モジュールが import する前提）
def embed_text(text: str, client: Optional[EmbeddingClient] = None) -> List[float]:
    c = client or create_embedding_client()
    return c.embed(text)

def embed_texts(texts: Sequence[str], client: Optional[EmbeddingClient] = None) -> List[List[float]]:
    c = client or create_embedding_client()
    return c.embed_texts(texts)


# ===============================
# Persistence
# ===============================

def _extract_message_fields(msg: Any) -> Tuple[Optional[str], Optional[str], str]:
    """
    ConversationMessage / dict など様々な形から
    (message_id, role, text) をロバストに抽出する。
    """
    # ORM オブジェクト互換
    message_id = getattr(msg, "id", None) or getattr(msg, "message_id", None)
    role = getattr(msg, "role", None) or getattr(msg, "speaker", None)
    text = (
        getattr(msg, "content", None)
        or getattr(msg, "message_text", None)
        or getattr(msg, "text", None)
    )

    # dict 互換
    if isinstance(msg, dict):
        message_id = msg.get("id") or msg.get("message_id") or message_id
        role = msg.get("role") or msg.get("speaker") or role
        text = msg.get("content") or msg.get("message_text") or msg.get("text") or text

    if text is None:
        text = ""
    else:
        text = str(text)

    if role is not None:
        role = str(role)

    if message_id is not None:
        message_id = str(message_id)

    return message_id, role, text


def _reflect_conversation_embeddings_table(bind_engine) -> Tuple[Table, dict]:
    """
    conversation_embeddings テーブルをリフレクションし、
    よくある列名を動的にマッピングして返す。
    返り値: (Table, column_map)
      column_map = {
        "session": "<session列名>",
        "message": "<message列名>",
        "role": "<role列名 or None>",
        "text": "<text列名 or None>",
        "embedding": "<埋め込み列名>",
      }
    """
    metadata = MetaData()
    table = Table("conversation_embeddings", metadata, autoload_with=bind_engine)
    cols = {c.name for c in table.columns}

    def pick(*candidates: str) -> Optional[str]:
        for name in candidates:
            if name in cols:
                return name
        return None

    col_session = pick("session_id", "conversation_id", "sessionId")
    col_message = pick("message_id", "messageId")
    col_role = pick("role", "speaker")
    col_text = pick("text", "message_text", "content")
    col_embedding = pick("embedding", "vector", "embedding_vector")

    if not col_session:
        raise RuntimeError("conversation_embeddings: session id column not found")
    if not col_message:
        raise RuntimeError("conversation_embeddings: message id column not found")
    if not col_embedding:
        raise RuntimeError("conversation_embeddings: embedding column not found")

    return table, {
        "session": col_session,
        "message": col_message,
        "role": col_role,
        "text": col_text,
        "embedding": col_embedding,
    }


def save_conversation_embeddings(
    session_id: Union[str, int],
    messages: Sequence[Any],
    client: Optional[EmbeddingClient] = None,
    db: Optional[Session] = None,
) -> None:
    """
    セッションに紐づくメッセージの埋め込みを conversation_embeddings に保存する。
    - 既存実装を壊さないため、ORM モデル `ConversationEmbedding` があればそれを利用。
    - 列名差異にロバストに対応するため、ORM が無い場合はリフレクションで列を解決。
    - 失敗しても例外を外に伝播させない方針は上位（呼び出し側の try/except）で実施済み。
    """
    own_session = False
    if db is None:
        db = SessionLocal()
        own_session = True

    try:
        c = client or create_embedding_client()

        # 対象行の抽出
        records: List[Tuple[Optional[str], Optional[str], str]] = []
        for m in messages:
            mid, role, text = _extract_message_fields(m)
            if not text:
                continue
            records.append((mid, role, text))

        if not records:
            return

        # 埋め込み生成
        texts = [t for (_mid, _role, t) in records]
        vectors = c.embed_texts(texts)

        # まず ORM モデルが存在すればそれを使う
        conversation_embedding_model = getattr(models, "ConversationEmbedding", None)

        if conversation_embedding_model is not None:
            for (mid, role, text), vec in zip(records, vectors):
                row = conversation_embedding_model(
                    session_id=str(session_id),
                    message_id=mid,
                    role=role,
                    text=text,
                    embedding=json.dumps(vec),  # DB 側が JSON/Text の想定（Array 型なら後段で反映）
                )
                db.add(row)
            db.commit()
            return

        # ORM が無ければテーブルをリフレクションして列に合わせて insert
        engine = db.get_bind()
        table, cmap = _reflect_conversation_embeddings_table(engine)

        payloads: List[dict] = []
        for (mid, role, text), vec in zip(records, vectors):
            row = {
                cmap["session"]: str(session_id),
                cmap["message"]: mid,
                cmap["embedding"]: json.dumps(vec),
            }
            if cmap["role"]:
                row[cmap["role"]] = role
            if cmap["text"]:
                row[cmap["text"]] = text
            payloads.append(row)

        if payloads:
            stmt = insert(table).values(payloads)
            db.execute(stmt)
            db.commit()

    finally:
        if own_session:
            db.close()
