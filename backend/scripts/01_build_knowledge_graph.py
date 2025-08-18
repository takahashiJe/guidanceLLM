# -*- coding: utf-8 -*-
"""
Knowledge Graph (RAG Vectorstore) Builder
-----------------------------------------
- 言語別(ja/en/zh)の Markdown 知識を読み込み、チャンク化してベクトル化、
  言語ごとのベクトルストアに永続化するスクリプト。
- 既存の実装方針を壊さずに、多言語化と Embeddings ファサード導入、
  永続パスの正規化（vectorstore/<lang>）のみ追加している。

実行例:
  python backend/scripts/01_build_knowledge_graph.py            # ja のみ
  python backend/scripts/01_build_knowledge_graph.py --lang en  # en のみ
  python backend/scripts/01_build_knowledge_graph.py --lang all # ja/en/zh 全て

環境変数:
  KNOWLEDGE_BASE  ... 既定: backend/worker/data/knowledge      （配下に ja/en/zh を持つ）
  VECTORSTORE_BASE ... 既定: backend/vectorstore               （配下に ja/en/zh を作成）
"""

from __future__ import annotations

import os
import re
import sys
import argparse
import hashlib
from pathlib import Path
from typing import List, Dict, Tuple

# =========================
# 設定（多言語＆環境変数対応）
# =========================
# 「ベース」ディレクトリ直下に ja/en/zh をぶら下げる想定
KNOWLEDGE_BASE = Path(
    os.getenv("KNOWLEDGE_BASE", "backend/worker/data/knowledge")
).resolve()

VECTORSTORE_BASE = Path(
    os.getenv("VECTORSTORE_BASE", "backend/vectorstore")
).resolve()

def knowledge_root_for(lang: str) -> Path:
    """言語別の知識ディレクトリ（例: backend/worker/data/knowledge/ja）"""
    return (KNOWLEDGE_BASE / lang).resolve()

def persist_dir_for(lang: str) -> Path:
    """言語別の保存先ディレクトリ（例: backend/vectorstore/ja）"""
    return (VECTORSTORE_BASE / lang).resolve()


# =========================
# 依存（アプリの埋め込み実装を使用）
# =========================
# PYTHONPATH=/app/backend を前提として、アプリ内の Embeddings ファサードを利用
from worker.app.services.embeddings import Embeddings

# =========================
# Vectorstore (ChromaDB)
# =========================
try:
    import chromadb
except ImportError as e:
    print("ERROR: chromadb が見つかりません。pyproject/requirements に chromadb を追加してください。", file=sys.stderr)
    raise

# =========================
# チャンク分割（依存を増やさない素朴な実装）
# =========================
def _read_markdown(path: Path) -> str:
    """UTF-8 前提で Markdown を読み込む。BOM は除去。"""
    text = path.read_text(encoding="utf-8")
    # Windows 由来の BOM を除去
    return text.lstrip("\ufeff")

def _split_markdown_to_chunks(text: str, *, chunk_size: int = 1200, overlap: int = 200) -> List[str]:
    """
    依存を増やさず、素朴な文字数ベースのチャンク分割を行う。
    - ヘッダ(#...)や空行である程度自然な区切りになるよう、改行でラフ分割→再結合。
    - chunk_size/overlap は文字数。tokenizer は使わない（安定性・再現性優先）。
    """
    # まずは段落ベースで軽く分割
    paragraphs = re.split(r"\n{2,}", text.strip(), flags=re.MULTILINE)

    # 段落を連結しつつ一定長でスライス
    chunks: List[str] = []
    buf = ""
    for p in paragraphs:
        if buf:
            candidate = buf + "\n\n" + p
        else:
            candidate = p

        if len(candidate) <= chunk_size:
            buf = candidate
        else:
            # buf を確定してスライス
            if buf:
                chunks.extend(_slice_with_overlap(buf, chunk_size, overlap))
                buf = ""
            # 残り段落自体が大きい場合は段落内でもスライス
            chunks.extend(_slice_with_overlap(p, chunk_size, overlap))

    if buf:
        chunks.extend(_slice_with_overlap(buf, chunk_size, overlap))

    # 空文字を除去
    return [c.strip() for c in chunks if c and c.strip()]

def _slice_with_overlap(text: str, chunk_size: int, overlap: int) -> List[str]:
    """単純な文字スライサー（overlap 付き）"""
    if chunk_size <= 0:
        return [text]
    if overlap < 0:
        overlap = 0

    res: List[str] = []
    start = 0
    N = len(text)
    step = max(chunk_size - overlap, 1)
    while start < N:
        end = min(start + chunk_size, N)
        res.append(text[start:end])
        if end == N:
            break
        start += step
    return res

def _hash_id(*parts: str) -> str:
    """安定した ID を生成（ファイルパス＋チャンク内容から）"""
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8", errors="ignore"))
    return h.hexdigest()

def _collect_md_files(root: Path) -> List[Path]:
    """root 以下の .md を再帰探索"""
    return sorted([p for p in root.rglob("*.md") if p.is_file()])


# =========================
# 言語単位のビルド処理
# =========================
def build_for_lang(*, lang: str, knowledge_root: Path, persist_dir: Path) -> None:
    """
    単一言語分のインデックスを構築する。
    - knowledge_root: 例) backend/worker/data/knowledge/ja
    - persist_dir   : 例) backend/vectorstore/ja
    """
    print(f"[{lang}] knowledge_root = {knowledge_root}")
    print(f"[{lang}] persist_dir   = {persist_dir}")

    if not knowledge_root.exists():
        print(f"[{lang}] 知識ディレクトリが存在しません。スキップ: {knowledge_root}")
        return

    persist_dir.mkdir(parents=True, exist_ok=True)

    md_files = _collect_md_files(knowledge_root)
    if not md_files:
        print(f"[{lang}] Markdown ファイルが見つかりません。スキップ。")
        return

    print(f"[{lang}] 発見ファイル数: {len(md_files)}")

    # Embeddings ファサードを使用（mxbai-embed-large / 1024 次元）
    embedder = Embeddings()

    # ChromaDB の永続クライアント
    client = chromadb.PersistentClient(path=str(persist_dir))
    # 言語ごとにコレクションを分ける（将来 en/zh を増やしても衝突しない）
    collection_name = f"knowledge_{lang}"
    # 既存があれば取得、なければ作成
    try:
        collection = client.get_collection(collection_name)
    except Exception:
        collection = client.create_collection(name=collection_name, metadata={"lang": lang})

    # ドキュメントを順にチャンク → 埋め込み → upsert
    upsert_total = 0
    for fpath in md_files:
        rel_path = str(fpath.relative_to(knowledge_root))
        raw_text = _read_markdown(fpath)
        if not raw_text.strip():
            continue

        chunks = _split_markdown_to_chunks(raw_text, chunk_size=1200, overlap=200)
        if not chunks:
            continue

        # メタデータ（検索時にファイル名等を戻せるように保持）
        metadatas: List[Dict[str, str]] = []
        ids: List[str] = []
        for idx, chunk in enumerate(chunks):
            cid = _hash_id(rel_path, str(idx), chunk)
            ids.append(cid)
            metadatas.append({
                "lang": lang,
                "source": rel_path,   # 例: "spots/spot_mototaki.md"
                "chunk_index": str(idx),
            })

        # 埋め込み（バッチ）
        embeddings = embedder.embed_texts(chunks, lang=lang)  # -> List[List[float]]

        # upsert
        collection.upsert(
            ids=ids,
            documents=chunks,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        upsert_total += len(chunks)

    print(f"[{lang}] upsert 完了: {upsert_total} チャンク")
    print(f"[{lang}] 永続化先: {persist_dir} / collection={collection_name}")


# =========================
# CLI
# =========================
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build knowledge graph (RAG vectorstore).")
    parser.add_argument(
        "--lang",
        choices=["ja", "en", "zh", "all"],
        default="ja",
        help="Which language to index (default: ja). Use 'all' to index ja/en/zh in sequence.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    langs = ["ja", "en", "zh"] if args.lang == "all" else [args.lang]

    for lang in langs:
        kroot = knowledge_root_for(lang)
        pdir = persist_dir_for(lang)
        pdir.mkdir(parents=True, exist_ok=True)

        print(f"[{lang}] ===== RAG Build Start =====")
        build_for_lang(lang=lang, knowledge_root=kroot, persist_dir=pdir)
        print(f"[{lang}] ===== RAG Build Done  =====\n")


if __name__ == "__main__":
    main()
