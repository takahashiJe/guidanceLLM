# -*- coding: utf-8 -*-
"""
Ollama (mxbai-embed-large) を使った言語別 Knowledge Vectorstore ビルドスクリプト（堅牢版/改）
- 入力:  /app/backend/worker/data/knowledge/{ja,en,zh}/ の .md/.mdx/.txt
- 出力:  /app/backend/worker/data/vectorstore/{lang}/ に Chroma 'knowledge_{lang}'
- 追加の堅牢化:
  - 空チャンクの徹底除外
  - 埋め込み失敗/空ベクトル/数値でない要素の除外
  - 次元不整合（ベクトル長が揃っていない）の除外
  - Upsert 直前に docs / embeddings の件数一致チェック（不一致は短い方に揃えて継続）
"""

from __future__ import annotations

import os
import sys
import glob
import hashlib
from dataclasses import dataclass
from typing import List, Dict, Tuple

# --- プロジェクト内インポート（PYTHONPATH=backend 前提） ---
from worker.app.services.embeddings import OllamaEmbeddingClient

try:
    import chromadb
    from chromadb import PersistentClient
    from chromadb.config import Settings
except Exception as e:
    print(f"[vectorstore-init] chromadb import failed: {e}", file=sys.stderr)
    raise

# =========================
# 設定
# =========================
SUPPORTED_LANGS = os.getenv("KNOWLEDGE_LANGS", "ja,en,zh").split(",")

KNOWLEDGE_BASE_DIR = os.getenv("KNOWLEDGE_BASE_DIR", "/app/backend/worker/data/knowledge")
VECTORSTORE_BASE_DIR = os.getenv("VECTORSTORE_BASE_DIR", "/app/backend/worker/data/vectorstore")

OLLAMA_BASE_URL = os.getenv("OLLAMA_HOST", "http://ollama:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "mxbai-embed-large")

MAX_CHARS = int(os.getenv("KNOWLEDGE_CHUNK_MAX_CHARS", "1200"))
OVERLAP = int(os.getenv("KNOWLEDGE_CHUNK_OVERLAP", "200"))
BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "64"))

# =========================
# ユーティリティ
# =========================
def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def _strip_front_matter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            return text[end + 4 :].lstrip("\n")
    return text

def _chunk(text: str, max_chars: int, overlap: int) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        chunk = text[start:end].strip()
        if chunk:  # 空文字は生成時点で除外
            chunks.append(chunk)
        if end == n:
            break
        start = max(0, end - overlap)
    return chunks

def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

@dataclass
class DocChunk:
    doc_id: str
    document: str
    metadata: Dict[str, str]

# =========================
# Chroma
# =========================
def _open_chroma(persist_dir: str) -> PersistentClient:
    os.makedirs(persist_dir, exist_ok=True)
    client = chromadb.PersistentClient(
        path=persist_dir,
        settings=Settings(anonymized_telemetry=False),
    )
    return client

# =========================
# ロード & 埋め込み & upsert
# =========================
def _load_lang_docs(lang: str) -> List[DocChunk]:
    lang_dir = os.path.join(KNOWLEDGE_BASE_DIR, lang)
    if not os.path.isdir(lang_dir):
        print(f"[vectorstore-init] found 0 files in {lang_dir}")
        return []

    paths = []
    paths += glob.glob(os.path.join(lang_dir, "**", "*.md"), recursive=True)
    paths += glob.glob(os.path.join(lang_dir, "**", "*.mdx"), recursive=True)
    paths += glob.glob(os.path.join(lang_dir, "**", "*.txt"), recursive=True)

    if not paths:
        print(f"[vectorstore-init] found 0 files in {lang_dir}")
        return []

    chunks: List[DocChunk] = []
    for p in sorted(paths):
        try:
            raw = _read_text(p)
            text = _strip_front_matter(raw)
            parts = _chunk(text, MAX_CHARS, OVERLAP)
            if not parts:
                continue

            # タイトル候補（先頭の # 見出し）
            title = None
            for line in text.splitlines():
                ls = line.strip()
                if ls.startswith("# "):
                    title = ls[2:].strip()
                    break

            for i, content in enumerate(parts):
                if not content or not content.strip():
                    continue
                chunks.append(
                    DocChunk(
                        doc_id=_sha1(f"{p}::{i}"),
                        document=content.strip(),
                        metadata={
                            "lang": lang,
                            "source_path": p,
                            "source_name": os.path.basename(p),
                            "chunk_index": str(i),
                            "title": title or os.path.basename(p),
                        },
                    )
                )
        except Exception as e:
            print(f"[vectorstore-init] read failed: {p} ({e})", file=sys.stderr)

    print(f"[vectorstore-init] {lang}: loaded {len(chunks)} chunks from {len(paths)} files")
    return chunks

def _validate_and_filter_vectors(
    docs: List[DocChunk], vectors: List[List[float]]
) -> Tuple[List[DocChunk], List[List[float]]]:
    """
    - 空リスト/None/非数値要素を含むベクトルを除外
    - ベクトル長の不整合がある場合は多数派の次元に合わせ、それ以外を除外
    """
    if not docs or not vectors:
        return [], []

    kept_docs: List[DocChunk] = []
    kept_vecs: List[List[float]] = []

    # 1) 明らかに不正なものを除外
    for d, v in zip(docs, vectors):
        if not isinstance(v, list) or len(v) == 0:
            continue
        try:
            _ = [float(x) for x in v]
        except Exception:
            continue
        kept_docs.append(d)
        kept_vecs.append(v)

    if not kept_docs:
        return [], []

    # 2) 次元チェック
    from collections import Counter
    dims = [len(v) for v in kept_vecs]
    dim_counter = Counter(dims)
    target_dim, _ = dim_counter.most_common(1)[0]

    final_docs: List[DocChunk] = []
    final_vecs: List[List[float]] = []
    removed_dim_mismatch = 0

    for d, v in zip(kept_docs, kept_vecs):
        if len(v) == target_dim:
            final_docs.append(d)
            final_vecs.append(v)
        else:
            removed_dim_mismatch += 1

    if removed_dim_mismatch > 0:
        print(f"[vectorstore-init] filtered {removed_dim_mismatch} vectors due to dim mismatch (target_dim={target_dim})")

    return final_docs, final_vecs

def _embed_batches(emb: OllamaEmbeddingClient, docs: List[DocChunk]) -> List[List[float]]:
    vecs: List[List[float]] = []
    if not docs:
        return vecs

    for i in range(0, len(docs), BATCH_SIZE):
        batch = docs[i : i + BATCH_SIZE]
        texts = [d.document for d in batch]
        valid_pairs = [(t, d) for t, d in zip(texts, batch) if isinstance(t, str) and t.strip()]
        if not valid_pairs:
            print(f"[vectorstore-init] embed {i}..{i+len(batch)-1}  skip(empty batch)")
            continue

        texts2 = [t for t, _ in valid_pairs]
        docs2 = [d for _, d in valid_pairs]

        try:
            v = emb.embed_texts(texts2)  # 1件ずつ内部で呼ぶ
        except Exception as e:
            print(f"[vectorstore-init] embedding failed at batch {i}: {e}", file=sys.stderr)
            v = []

        docs2, v = _validate_and_filter_vectors(docs2, v)
        vecs.extend(v)
        print(f"[vectorstore-init] embed {i}..{i+len(batch)-1}  ok={len(v)}")
    return vecs

def _upsert_lang(lang: str, docs: List[DocChunk]) -> None:
    if not docs:
        print(f"[vectorstore-init] {lang}: no docs to upsert (skip)")
        return

    emb = OllamaEmbeddingClient(base_url=OLLAMA_BASE_URL, model=EMBED_MODEL)
    vectors = _embed_batches(emb, docs)

    docs, vectors = _validate_and_filter_vectors(docs, vectors)
    if not docs or not vectors:
        print(f"[vectorstore-init] {lang}: nothing to upsert after filtering (skip)")
        return

    if len(docs) != len(vectors):
        n = min(len(docs), len(vectors))
        print(f"[vectorstore-init] WARN: docs({len(docs)}) != vectors({len(vectors)}), truncate to {n}")
        docs = docs[:n]
        vectors = vectors[:n]

    persist_dir = os.path.join(VECTORSTORE_BASE_DIR, lang)
    client = _open_chroma(persist_dir)
    collection = client.get_or_create_collection(
        name=f"knowledge_{lang}",
        metadata={
            "lang": lang,
            "embed_model": EMBED_MODEL,
        },
    )

    collection.upsert(
        ids=[d.doc_id for d in docs],
        embeddings=vectors,
        documents=[d.document for d in docs],
        metadatas=[d.metadata for d in docs],
    )
    try:
        total = collection.count()
    except Exception:
        total = "unknown"
    print(f"[vectorstore-init] {lang}: upserted={len(docs)} total={total}")

def main():
    print(f"[vectorstore-init] start: KNOWLEDGE_BASE_DIR={KNOWLEDGE_BASE_DIR}")
    total_chunks = 0
    for raw_lang in SUPPORTED_LANGS:
        lang = raw_lang.strip()
        docs = _load_lang_docs(lang)
        total_chunks += len(docs)
        _upsert_lang(lang, docs)
    print(f"[vectorstore-init] done. total_chunks={total_chunks}")

if __name__ == "__main__":
    main()
