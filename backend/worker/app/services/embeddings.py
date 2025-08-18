# worker/app/services/embeddings.py
from __future__ import annotations

import hashlib
import math
import os
from functools import lru_cache
from typing import Iterable, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception:  # numpy が無いケースでも落ちないように
    np = None

# sentence-transformers があれば優先利用（無ければハッシュ埋め込みにフォールバック）
try:
    from sentence_transformers import SentenceTransformer
    _HAS_ST = True
except Exception:
    SentenceTransformer = None  # type: ignore
    _HAS_ST = False

# --- ここから公開インターフェース -------------------------------------------------
__all__ = [
    "EmbeddingClient",
    "create_embedding_client",
    "embed_text",
    "embed_text_batch",
    "cosine_similarities",
    "save_conversation_embeddings",
]

# =============================================================================
# Embedding クライアント
# =============================================================================

class EmbeddingClient:
    """
    統一インターフェース:
      - embed(texts: List[str]) -> List[List[float]]
    実装は Sentence-Transformers があればそれを使い、無ければハッシュベースで決定論的に生成。
    """
    def __init__(self, model_name: Optional[str] = None, dim: int = 384):
        self.dim = dim
        self._mode = "hash"
        self._model = None

        # 優先的に ST を使う（環境変数で明示指定可能）
        wanted = model_name or os.getenv("EMBEDDING_MODEL") or ""
        if _HAS_ST:
            try:
                self._model = SentenceTransformer(
                    wanted or "sentence-transformers/all-MiniLM-L6-v2"
                )
                # ST の出力次元を反映
                test = self._model.encode(["test"], normalize_embeddings=True)
                if isinstance(test, list):
                    d = len(test[0])
                else:
                    d = int(test.shape[-1])
                self.dim = d
                self._mode = "st"
            except Exception:
                # モデル取得失敗時はフォールバック
                self._model = None
                self._mode = "hash"

    def _hash_embed_one(self, text: str) -> List[float]:
        """
        依存のない決定論的埋め込み（フォールバック用）
        - テストや軽量実行で落ちないことを目的
        - 出力は self.dim 次元、L2 正規化
        """
        if not text:
            text = ""
        h = hashlib.sha256(text.encode("utf-8")).digest()
        # digest を繰り返して dim を満たす
        buf = bytearray()
        while len(buf) < self.dim * 4:
            h = hashlib.sha256(h).digest()
            buf.extend(h)

        # 4 バイトずつ float に（0..255 を 0..1 に寄せる）
        vals = []
        for i in range(self.dim):
            # 簡単な整数→浮動小数変換（偏り低減のために 4 バイト使用）
            chunk = buf[i * 4 : (i + 1) * 4]
            iv = int.from_bytes(chunk, "little", signed=False)
            # 0..(2^32-1) を -0.5..+0.5 に射影
            v = (iv / 4294967295.0) - 0.5
            vals.append(v)

        # L2 正規化
        norm = math.sqrt(sum(v * v for v in vals)) or 1.0
        return [v / norm for v in vals]

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        if not texts:
            return []
        if self._mode == "st" and self._model is not None:
            # Sentence-Transformers
            vecs = self._model.encode(
                list(texts),
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            if isinstance(vecs, list):  # 古いバージョン互換
                return [list(map(float, v)) for v in vecs]
            return [list(map(float, v)) for v in vecs.tolist()]
        # フォールバック（ハッシュ）
        return [self._hash_embed_one(t) for t in texts]


@lru_cache(maxsize=1)
def create_embedding_client(model_name: Optional[str] = None) -> EmbeddingClient:
    """
    シングルトン生成。model_name 指定が無ければ環境変数／デフォルトを参照。
    """
    return EmbeddingClient(model_name=model_name)

# =============================================================================
# ユーティリティ関数（公開 API）
# =============================================================================

def embed_text(text: str, model_name: Optional[str] = None) -> List[float]:
    """
    information_nodes などが import する公開関数（欠けていたため ImportError になっていた）。
    単文をベクタ化して返す。
    """
    client = create_embedding_client(model_name=model_name)
    res = client.embed([text])
    return res[0] if res else []


def embed_text_batch(texts: Sequence[str], model_name: Optional[str] = None) -> List[List[float]]:
    """
    まとめてベクタ化。既存コードが利用している場合があるため維持。
    """
    client = create_embedding_client(model_name=model_name)
    return client.embed(list(texts))


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if np is not None:
        va = np.asarray(a, dtype=float)
        vb = np.asarray(b, dtype=float)
        denom = (np.linalg.norm(va) * np.linalg.norm(vb)) or 1.0
        return float(np.dot(va, vb) / denom)
    # numpy 無しでも計算できる簡易版
    num = sum(x * y for x, y in zip(a, b))
    denom = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    denom = denom or 1.0
    return float(num / denom)


def cosine_similarities(query_vec: Sequence[float], matrix: Sequence[Sequence[float]]) -> List[float]:
    """
    クエリベクタと行列（各要素がベクタ）のコサイン類似度を並べて返す。
    information_nodes から利用される想定の公開関数。
    """
    return [_cosine(query_vec, row) for row in matrix]

# =============================================================================
# 保存系（既存互換）
# =============================================================================
# ここは既存の DB 保存/更新ロジックを壊さないように関数シグネチャを維持します。
# 具体的なモデルは shared.app.models にあり、既存テスト（embeddings_smoke）では
# 「正常に insert / select できること」のみを緩く確認している前提です。

def save_conversation_embeddings(
    db_session,
    session_id: str,
    entries: Sequence[Tuple[str, str]],
    *,
    model_name: Optional[str] = None,
) -> None:
    """
    会話（role, content）の配列を受け取り、埋め込みを計算して DB に保存するユーティリティ。
    - db_session: SQLAlchemy Session を想定（既存コード準拠）
    - session_id: 会話セッション ID
    - entries: [(role, content), ...]
    - model_name: 使用モデルを上書きしたい場合に指定
    既存の処理（テーブル/カラム設計）に合わせ、shared.app.models を参照して保存します。
    """
    from shared.app import models  # 既存のモデル定義を利用
    from datetime import datetime

    texts: List[str] = [c for (_, c) in entries]
    if not texts:
        return

    vecs = embed_text_batch(texts, model_name=model_name)

    # 既存のテーブル名/カラム名に合わせる（存在しない場合は例外になりテストで気付ける）
    # 典型的には ConversationEmbedding モデルを想定:
    #   - id (autoincrement)
    #   - session_id (str)
    #   - role (str)
    #   - content (str)
    #   - vector (Array[float] or BLOB/JSON)
    #   - created_at (datetime)
    # など。実際の型は models 側に従います。
    created_at = datetime.utcnow()
    for (role, content), vec in zip(entries, vecs):
        rec = models.ConversationEmbedding(
            session_id=session_id,
            role=role,
            content=content,
            vector=vec,         # SQLAlchemy 側で JSON/Array などにマッピングされている想定
            created_at=created_at,
        )
        db_session.add(rec)

    db_session.commit()
