# -*- coding: utf-8 -*-
"""
DB 初期化スクリプト（冪等）
- 既存テーブル作成
- pgvector 拡張作成（存在しなくても続行）
- conversation_embeddings の埋め込み列を Vector(1024) へ強制整合
- IVFFLAT インデックス作成（存在しなければ）
"""

from __future__ import annotations

import os
import sys
import traceback
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# 共有モデルを利用
from shared.app.models import Base

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("[init_db_script] ERROR: DATABASE_URL is not set.", file=sys.stderr)
    sys.exit(1)

engine: Engine = create_engine(DATABASE_URL, future=True)


@contextmanager
def begin_conn(e: Engine):
    with e.begin() as conn:
        yield conn


def create_extension_vector(e: Engine) -> None:
    # pgvector が使える環境なら拡張を作成（なければ警告のみで続行）
    with begin_conn(e) as conn:
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        except Exception as err:
            print(f"[init_db_script] NOTE: pgvector extension not available: {err}")


def create_tables(e: Engine) -> None:
    # 既存テーブル作成（models.py に定義された全テーブル）
    Base.metadata.create_all(bind=e)


def _convemb_udt_name(e: Engine) -> str | None:
    # conversation_embeddings.embedding の実際の型名を取得（vector/jsonb 等）
    q = text("""
        SELECT udt_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'conversation_embeddings'
          AND column_name = 'embedding'
        LIMIT 1;
    """)
    with begin_conn(e) as conn:
        row = conn.execute(q).fetchone()
        return row[0] if row else None


def _convemb_is_empty(e: Engine) -> bool:
    with begin_conn(e) as conn:
        row = conn.execute(text("SELECT COUNT(*) FROM conversation_embeddings;")).fetchone()
        return (row[0] == 0) if row else True


def ensure_convemb_vector_and_index(e: Engine) -> None:
    """
    - 埋め込み列が jsonb のままなら、空テーブルであればドロップ→再作成して vector(1024) に。
      （データがある場合は温存のためスキップし、検索性能は落ちるが動作は継続）
    - 列が vector の場合は IVFFLAT インデックスを作成（存在しない場合のみ）。
    """
    udt = _convemb_udt_name(e)
    if udt is None:
        # テーブル自体が無いなら何もしない（create_all が後で作る前提）
        return

    if udt == "jsonb":
        if _convemb_is_empty(e):
            # 破壊的だが空なら安全：ドロップ→create_all で再作成（models は pgvector を前提にしておく）
            print("[init_db_script] conversation_embeddings is JSONB & empty -> drop and recreate as vector")
            with begin_conn(e) as conn:
                conn.execute(text("DROP TABLE conversation_embeddings;"))
            # 再作成
            Base.metadata.create_all(bind=e)
        else:
            print("[init_db_script] WARNING: embeddings column is JSONB and table has rows -> keep JSONB (no IVFFLAT).")
            return  # データ温存のため何もしない

    # ここまで来たら埋め込み列は vector のはず。IVFFLAT を作成（なければ）
    with begin_conn(e) as conn:
        try:
            conn.execute(text("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = 'ix_convemb_embedding_ivfflat') THEN
                    CREATE INDEX ix_convemb_embedding_ivfflat
                    ON conversation_embeddings
                    USING ivfflat (embedding vector_l2_ops) WITH (lists = 100);
                END IF;
            END
            $$;
            """))
        except Exception as err:
            print(f"[init_db_script] NOTE: skip IVFFLAT index (pgvector/column mismatch?): {err}")


def main() -> None:
    try:
        create_extension_vector(engine)  # pgvector 拡張
        create_tables(engine)            # 既存：全テーブル作成
        ensure_convemb_vector_and_index(engine)  # JSONB→vector 修正 & インデックス
        print("[init_db_script] DB init completed.")
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
