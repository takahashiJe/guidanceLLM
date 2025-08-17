# -*- coding: utf-8 -*-
import pytest
from .conftest import _url, wait_chat_result

pytestmark = pytest.mark.e2e

def test_planning_flow(session_requests, auth_headers):
    # 1) セッション作成
    r = session_requests.post(_url("/api/v1/sessions/create"), headers=auth_headers)
    assert r.status_code in (200, 201), r.text
    session_id = r.json()["session_id"]

    # 2) 固有名詞問い合わせ（例：「法体の滝の詳細」）
    r = session_requests.post(_url("/api/v1/chat/message"), headers=auth_headers, json={
        "session_id": session_id,
        "lang": "ja",
        "input_mode": "text",
        "message_text": "法体の滝の詳細を教えて"
    })
    assert r.status_code in (200, 202), r.text
    task_id = r.json().get("task_id")
    detail = wait_chat_result(session_requests, task_id)
    assert "final_response" in detail

    # 3) 計画編集系（自然言語のままでもOKな設計だが、ここではAPIの対話経由で操作）
    #    例：「法体の滝を追加して」
    r = session_requests.post(_url("/api/v1/chat/message"), headers=auth_headers, json={
        "session_id": session_id,
        "lang": "ja",
        "input_mode": "text",
        "message_text": "計画に法体の滝を追加して"
    })
    assert r.status_code in (200, 202), r.text
    task_id = r.json().get("task_id")
    added = wait_chat_result(session_requests, task_id)
    assert "final_response" in added

    # 4) 暫定ルート（Routing Service 連携が走る想定）
    r = session_requests.post(_url("/api/v1/chat/message"), headers=auth_headers, json={
        "session_id": session_id,
        "lang": "ja",
        "input_mode": "text",
        "message_text": "いまの計画ルートを作って"
    })
    assert r.status_code in (200, 202), r.text
    task_id = r.json().get("task_id")
    route = wait_chat_result(session_requests, task_id)
    assert "final_response" in route

    # 5) 計画要約（LLM による自然言語のサマリ）
    r = session_requests.post(_url("/api/v1/chat/message"), headers=auth_headers, json={
        "session_id": session_id,
        "lang": "ja",
        "input_mode": "text",
        "message_text": "計画の要約を教えて"
    })
    assert r.status_code in (200, 202), r.text
    task_id = r.json().get("task_id")
    summary = wait_chat_result(session_requests, task_id)
    assert "final_response" in summary
    assert len(summary["final_response"]) > 0
