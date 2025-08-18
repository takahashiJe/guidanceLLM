# backend/scripts/01_build_knowledge_graph.py
# ------------------------------------------------------------
# 役割:
#  - knowledge ディレクトリ (ja/en/zh) の Markdown を読み込み
#  - シンプルに見出し/段落でチャンク化
#  - Embeddings (mxbai-embed-large) でベクトル化
#  - ChromaDB (persistent) に upsert（冪等）
#
# 既存方針の踏襲:
#  - 永続先は vectorstore/ja を使用（現状のストレージ位置に合わせる）
#  - 将来的に言語別のベクトルストアへ切り出す拡張ポイントをコメントで明記
#
# 実行前提:
#  - PYTHONPATH=backend が設定されていること
#  - Ollama / mxbai-embed-large が利用可能 (Embeddings クラスが内部で使用)
#  - chromadb がインストール済み
# ------------------------------------------------------------

from __future__ import annotations

import os
import re
import sys
import hashlib
import json
from pathlib import Path
from typing import Dict, List, Tuple

# Embeddings は会話埋め込みでも使用している本番実装をそのまま使う
from worker.app.services.embeddings import Embeddings

# ChromaDB（ローカル永続）
import chromadb
from chromadb.config import Settings


# ==========================
# 設定値（環境変数で上書き可）
# ==========================

# 知識ソースのルート（既存の構成に合わせて worker/app 配下を既定値に）
KNOWLEDGE_ROOT = Path(
    os.getenv(
        "KNOWLEDGE_ROOT",
        "backend/worker/app/data/knowledge",
    )
).resolve()

# 永続先（現状の方針に合わせ vectorstore/ja に保存）
# ※将来 en/zh を別に分けるときはここを言語別に切り替えるだけでよい。
PERSIST_DIR = Path(
    os.getenv(
        "VECTORSTORE_DIR",
        "backend/vectorstore/ja",
    )
).resolve()

# Chroma コレクション名（固定でOK。メタデータに model/version/lang を付与する）
COLLECTION_NAME = os.getenv("VECTORSTORE_COLLECTION", "knowledge_v1")

# 再構築フラグ（1 のとき、コレクションを作り直す）
REBUILD = os.getenv("KNOWLEDGE_REBUILD", "0") == "1"

# チャンク関連
MAX_CHARS_PER_CHUNK = int(os.getenv("MAX_CHARS_PER_CHUNK", "1200"))  # 文字ベースでざっくり制限
MIN_CHARS_PER_CHUNK = int(os.getenv("MIN_CHARS_PER_CHUNK", "300"))

# 取り込む言語サブフォルダ（現状は ja/en/zh 全て走査する）
LANG_DIRS = os.getenv("KNOWLEDGE_LANGS", "ja,en,zh").split(",")

# 埋め込みモデル名（Embeddings 側の実装に準拠）
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "mxbai-embed-large")


# ==========================
# ユーティリティ
# ==========================

def _read_text(path: Path) -> str:
    """UTF-8 で Markdown を読み込む"""
    with path.open("r", encoding="utf-8") as f:
        return f.read()


def _split_markdown_to_chunks(text: str) -> List[Tuple[str, str]]:
    """
    Markdown をチャンクに分割する。
    - 大見出し/中見出し（#, ##, ###）を境に分割しつつ、段落でまとめる
    - 1チャンクの最大文字数を超えたら分割
    戻り値: List[(section_title, chunk_text)]
    """
    lines = text.splitlines()
    chunks: List[Tuple[str, str]] = []

    current_title = "General"
    current_buf: List[str] = []

    header_re = re.compile(r"^\s{0,3}(#{1,3})\s+(.*)$")

    def flush(force=False):
        """current_buf を適切なサイズで細分化して chunks に積む"""
        nonlocal current_buf, current_title, chunks
        buf_text = "\n".join(current_buf).strip()
        if not buf_text:
            current_buf = []
            return

        if len(buf_text) <= MAX_CHARS_PER_CHUNK:
            chunks.append((current_title, buf_text))
        else:
            # 長すぎる場合は素朴に分割
            start = 0
            while start < len(buf_text):
                end = min(start + MAX_CHARS_PER_CHUNK, len(buf_text))
                piece = buf_text[start:end]
                # できるだけ文末で切る（句点で後ろに寄せる）
                last_period = piece.rfind("。")
                if last_period > MIN_CHARS_PER_CHUNK:
                    end = start + last_period + 1
                    piece = buf_text[start:end]
                chunks.append((current_title, piece.strip()))
                start = end
        current_buf = []

    for line in lines:
        m = header_re.match(line)
        if m:
            # 新しい見出し
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            # レベルは使わないが、必要ならメタに入れても良い
            current_title = title or f"Section (h{level})"
        else:
            current_buf.append(line)

    flush(force=True)
    # 短すぎるチャンクを上と結合（雑に最適化）
    merged: List[Tuple[str, str]] = []
    for title, body in chunks:
        if merged and len(body) < MIN_CHARS_PER_CHUNK:
            prev_title, prev_body = merged.pop()
            merged.append((prev_title, (prev_body + "\n" + body).strip()))
        else:
            merged.append((title, body))

    return merged


def _make_chunk_id(lang: str, rel_path: str, idx: int, body: str) -> str:
    """
    冪等な upsert のための安定IDを生成。
    フォーマット: f"{lang}:{rel_path}::{idx}:{sha1(body)[:8]}"
    """
    h = hashlib.sha1(body.encode("utf-8")).hexdigest()[:8]
    return f"{lang}:{rel_path}::{idx}:{h}"


def _log(s: str):
    print(f"[build_knowledge] {s}")


# ==========================
# メイン処理
# ==========================

def main() -> None:
    # ルート存在確認
    if not KNOWLEDGE_ROOT.exists():
        raise RuntimeError(f"KNOWLEDGE_ROOT が見つかりません: {KNOWLEDGE_ROOT}")

    PERSIST_DIR.mkdir(parents=True, exist_ok=True)

    # Chroma の persistent クライアント
    client = chromadb.PersistentClient(
        path=str(PERSIST_DIR),
        settings=Settings(allow_reset=True),
    )

    # REBUILD 指定時はコレクションを作り直す
    if REBUILD:
        try:
            client.delete_collection(COLLECTION_NAME)
            _log(f"collection '{COLLECTION_NAME}' deleted (REBUILD=1)")
        except Exception:
            pass

    # コレクション作成/取得
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={
            "embedding_model": EMBED_MODEL,
            "version": "v1",
            "note": "knowledge graph chunks (ja/en/zh); persisted under vectorstore/ja by current policy",
        },
    )

    embedder = Embeddings()  # 既存の本実装（mxbai-embed-large）を利用

    total_files = 0
    total_chunks = 0

    for lang in LANG_DIRS:
        lang_dir = (KNOWLEDGE_ROOT / lang).resolve()
        if not lang_dir.exists():
            _log(f"lang dir not found (skip): {lang_dir}")
            continue

        md_files = list(lang_dir.rglob("*.md"))
        _log(f"lang={lang} files={len(md_files)}")
        for md_path in md_files:
            rel_path = str(md_path.relative_to(KNOWLEDGE_ROOT)).replace("\\", "/")
            text = _read_text(md_path)
            chunks = _split_markdown_to_chunks(text)

            if not chunks:
                continue

            # ドキュメント群をバッチ upsert
            ids: List[str] = []
            documents: List[str] = []
            metadatas: List[Dict] = []

            for idx, (title, body) in enumerate(chunks):
                cid = _make_chunk_id(lang, rel_path, idx, body)
                ids.append(cid)
                documents.append(body)
                metadatas.append(
                    {
                        "lang": lang,
                        "source_path": rel_path,
                        "section_title": title,
                        # 拡張ポイント: 将来 en/zh を別 vectorstore にするなら、
                        # ここでは同じ metadata を維持しつつ PERSIST_DIR を言語別に切り替える。
                    }
                )

            # 埋め込みを生成（Embeddings の同一実装を使うことで、会話/知識で次元やモデル名が一致）
            # Embeddings に複数テキスト用メソッドがない場合は、シンプルにループで生成
            try:
                if hasattr(embedder, "embed_texts"):
                    vectors = embedder.embed_texts(documents)  # type: ignore[attr-defined]
                else:
                    vectors = [embedder.embed_text(doc) for doc in documents]
            except Exception as e:
                _log(f"embedding failed for {rel_path}: {e}")
                continue

            # Chroma へ upsert（冪等）
            collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=vectors,
            )

            total_files += 1
            total_chunks += len(documents)
            _log(f"upserted: {rel_path} chunks={len(documents)}")

    _log(f"done. files={total_files} chunks={total_chunks}")
    _log(f"persist dir: {PERSIST_DIR}")
    _log(f"collection: {COLLECTION_NAME}")


if __name__ == "__main__":
    # ルートからの相対実行でも動くよう、最低限の案内を出す
    _log(f"KNOWLEDGE_ROOT={KNOWLEDGE_ROOT}")
    _log(f"PERSIST_DIR={PERSIST_DIR}")
    _log(f"REBUILD={REBUILD}")
    try:
        main()
    except Exception as e:
        _log(f"error: {e}")
        sys.exit(1)
