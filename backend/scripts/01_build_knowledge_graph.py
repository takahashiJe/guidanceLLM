# -*- coding: utf-8 -*-
"""
RAG 取り込み（冪等）
- KNOWLEDGE_BASE_DIR 直下の .md をベクトル化し、VECTORSTORE_DIR に蓄積
- 既存が空でなければスキップ（RAG_REBUILD_IF_EMPTY_ONLY=true のとき）
"""
import os
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

KNOWLEDGE_BASE_DIR = Path(os.getenv("KNOWLEDGE_BASE_DIR", "/app/worker/app/data/knowledge/ja"))
VECTORSTORE_DIR = Path(os.getenv("VECTORSTORE_DIR", "/app/worker/app/data/vectorstore/ja"))
RAG_BATCH_SIZE = int(os.getenv("RAG_BATCH_SIZE", "50"))
RAG_REBUILD_IF_EMPTY_ONLY = os.getenv("RAG_REBUILD_IF_EMPTY_ONLY", "true").lower() == "true"


def main():
    VECTORSTORE_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(VECTORSTORE_DIR))
    collection_name = f"knowledge_{os.getenv('KNOWLEDGE_LANG','ja')}"
    col = client.get_or_create_collection(name=collection_name)

    # 既存が空でない場合はスキップ
    if RAG_REBUILD_IF_EMPTY_ONLY and col.count() > 0:
        print("Vectorstore already has data. Skip.")
        return

    model = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    md_files = list(KNOWLEDGE_BASE_DIR.rglob("*.md"))
    print(f"Found {len(md_files)} files.")

    docs, ids, metas = [], [], []
    for md in md_files:
        text = md.read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            continue
        docs.append(text)
        ids.append(md.as_posix())
        metas.append({"path": md.as_posix()})

        if len(docs) >= RAG_BATCH_SIZE:
            emb = model.encode(docs, normalize_embeddings=True).tolist()
            col.upsert(documents=docs, ids=ids, metadatas=metas, embeddings=emb)
            docs, ids, metas = [], [], []

    if docs:
        emb = model.encode(docs, normalize_embeddings=True).tolist()
        col.upsert(documents=docs, ids=ids, metadatas=metas, embeddings=emb)

    print("Vectorstore build done.")


if __name__ == "__main__":
    main()
