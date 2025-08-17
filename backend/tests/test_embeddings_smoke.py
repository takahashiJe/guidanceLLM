# -*- coding: utf-8 -*-
"""
埋め込み長期記憶テーブルの存在と基本的な読み書き可否をスモーク確認。
- 先に API でセッションを作成して session_id を取得（FK制約に対応）
- その後、psycopg2 で conversation_embeddings へ INSERT/SELECT
"""
import os
import datetime
import numpy as np
import psycopg2

from fastapi.testclient import TestClient
from api_gateway.app.main import app
from .conftest import register_and_login


def _get_session_id_via_api() -> str:
    client = TestClient(app)
    _, tokens = register_and_login(client)
    at = tokens["access_token"]
    headers = {"Authorization": f"Bearer {at}"}
    r = client.post("/api/v1/sessions/create", headers=headers)
    assert r.status_code in (200, 201), r.text
    return r.json()["session_id"]


def test_conversation_embeddings_table_exists_and_insert_select():
    # まず API でセッションを作成して正当な session_id を得る
    session_id = _get_session_id_via_api()

    # DATABASE_URL は "postgresql+psycopg2://..." 形式の想定なので psycopg2 用に置換
    dsn = os.getenv("DATABASE_URL", "postgresql://chokai_user:jun@db:5432/chokai_db")
    dsn = dsn.replace("+psycopg2", "")
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    # テーブル存在チェック
    cur.execute("""
        SELECT 1
        FROM information_schema.tables
        WHERE table_name = 'conversation_embeddings'
        LIMIT 1;
    """)
    assert cur.fetchone(), "conversation_embeddings テーブルがありません。"

    # 1レコード挿入（FK: session_id を正当なものに）
    cur.execute("""
        INSERT INTO conversation_embeddings
        (session_id, speaker, lang, ts, text, embedding_version, embedding)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        session_id,
        "user",
        "ja",
        datetime.datetime.utcnow(),
        "テスト挿入",
        "mxbai-embed-large",
        list(np.zeros(1024, dtype=float)),  # pgvector(vector(1024)) に合わせる
    ))
    new_id = cur.fetchone()[0]
    conn.commit()

    # 取得してみる
    cur.execute("SELECT id, session_id, speaker, lang FROM conversation_embeddings WHERE id=%s", (new_id,))
    row = cur.fetchone()
    assert row and row[1] == session_id

    # 後片付け
    cur.execute("DELETE FROM conversation_embeddings WHERE id=%s", (new_id,))
    conn.commit()
    cur.close()
    conn.close()
