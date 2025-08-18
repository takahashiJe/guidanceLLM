# worker/app/services/embeddings.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Optional, Protocol, runtime_checkable, Iterable, Tuple
import os

try:
    # sentence-transformers がある前提の本実装
    from sentence_transformers import SentenceTransformer
except Exception:
    SentenceTransformer = None  # 実行環境により未導入のケースを許容


# ========================
# インターフェイス（互換維持）
# ========================
@runtime_checkable
class EmbeddingClient(Protocol):
    """
    既存コードが import することを想定したプロトコル。
    具象実装は下の OllamaEmbeddingClient / SentenceTransformerClient など。
    """
    def embed(self, text: str) -> List[float]: ...
    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]: ...


# ========================
# 具象実装
# ========================
@dataclass
class SentenceTransformerClient:
    """
    sentence-transformers を使う本実装。
    """
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    _model: Optional["SentenceTransformer"] = None

    def __post_init__(self) -> None:
        if SentenceTransformer is None:
            raise RuntimeError(
                "sentence-transformers が利用できません。"
                "pyproject.toml の依存関係に sentence-transformers を追加し、"
                "ワーカーイメージを再ビルドしてください。"
            )
        self._model = SentenceTransformer(self.model_name)

    def embed(self, text: str) -> List[float]:
        return list(map(float, self._model.encode([text])[0]))  # type: ignore[union-attr]

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        arr = self._model.encode(list(texts))  # type: ignore[union-attr]
        return [list(map(float, row)) for row in arr]


@dataclass
class OllamaEmbeddingClient:
    """
    既存コードとの互換シンボル。環境に応じて実際は SentenceTransformer を使用。
    （Ollama 連携を行いたい場合はここに HTTP 呼び出し等の実装を入れる）
    """
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    _impl: EmbeddingClient = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # ひとまず ST ベースの実装にフォールバック
        self._impl = SentenceTransformerClient(self.model_name)

    def embed(self, text: str) -> List[float]:
        return self._impl.embed(text)

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        return self._impl.embed_batch(texts)


# 互換のためにトップレベルにシンボルを公開しておく
# - 過去コードが `from worker.app.services.embeddings import EmbeddingClient`
#   としても ImportError にならないようにする
# - 実体は Protocol + 具象（OllamaEmbeddingClient / SentenceTransformerClient）
EmbeddingClient = EmbeddingClient  # type: ignore[assignment]


# ========================
# ユーティリティ
# ========================
def create_embedding_client(model_name: Optional[str] = None) -> EmbeddingClient:
    """
    既存のファクトリ。環境変数 EMBEDDING_MODEL も考慮。
    """
    model = model_name or os.getenv("EMBEDDING_MODEL") or "sentence-transformers/all-MiniLM-L6-v2"
    return OllamaEmbeddingClient(model)


def embed_texts(texts: Sequence[str], client: Optional[EmbeddingClient] = None) -> List[List[float]]:
    """
    テキスト配列を一括ベクトル化。
    """
    if client is None:
        client = create_embedding_client()
    return client.embed_batch(texts)


# ========================
# 会話埋め込みの保存（本実装）
# ========================
def _iter_message_texts_for_embedding(messages: Iterable[dict]) -> Iterable[Tuple[str, str]]:
    """
    与えられた会話メッセージ（辞書想定）から (role, content) ペアを抽出する。
    role: "user" / "assistant" 等
    content: 埋め込み対象テキスト
    """
    for m in messages:
        role = str(m.get("role") or m.get("type") or m.get("speaker") or "user")
        content = str(m.get("content") or m.get("text") or m.get("message") or "").strip()
        if content:
            yield role, content


def save_conversation_embeddings(
    session_id: str,
    messages: Sequence[dict],
    client: Optional[EmbeddingClient] = None,
) -> int:
    """
    既存の目的を満たす「会話内容のベクトル保存」本実装。
    - shared.app.database のセッションを使って conversation_embeddings テーブルへ保存する前提。
    - 既存スキーマを壊さないよう、存在チェック＋挿入に留める。
    - 返り値: 保存したレコード件数
    """
    from sqlalchemy import inspect, Table, Column, Integer, String, LargeBinary, MetaData, insert
    from sqlalchemy.orm import Session
    from shared.app.database import SessionLocal, engine

    # 埋め込みテキストの抽出
    pairs: List[Tuple[str, str]] = list(_iter_message_texts_for_embedding(messages))
    if not pairs:
        return 0

    # ベクトル計算
    if client is None:
        client = create_embedding_client()
    vectors = embed_texts([p[1] for p in pairs], client=client)

    # 既存テーブル存在確認（壊さない）
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "conversation_embeddings" not in tables:
        # 既存マイグレーションに任せる。なければ作成。（破壊的変更は避ける）
        meta = MetaData()
        Table(
            "conversation_embeddings",
            meta,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("session_id", String(64), index=True, nullable=False),
            Column("role", String(32), nullable=False),
            Column("content", String, nullable=False),
            Column("vector", LargeBinary, nullable=False),  # numpy.tofile 由来のバイナリなど
        )
        meta.create_all(engine)
        # 再読込
        tables = set(inspector.get_table_names())

    meta = MetaData()
    conv_tbl = Table("conversation_embeddings", meta, autoload_with=engine)

    # 保存（既存に追加）
    import numpy as np
    import io

    inserted = 0
    with SessionLocal() as db:  # type: Session
        for (role, text), vec in zip(pairs, vectors):
            # ベクトルはバイナリで保存（可逆）
            arr = np.asarray(vec, dtype=np.float32)
            buf = io.BytesIO()
            arr.tofile(buf)
            payload = {
                "session_id": session_id,
                "role": role,
                "content": text,
                "vector": buf.getvalue(),
            }
            db.execute(insert(conv_tbl).values(**payload))
            inserted += 1
        db.commit()
    return inserted
