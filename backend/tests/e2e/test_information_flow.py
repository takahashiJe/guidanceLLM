# -*- coding: utf-8 -*-
import os
import pytest
import requests

from .conftest import _url, wait_chat_result

pytestmark = pytest.mark.e2e

def test_information_flow(session_requests, auth_headers):
    # 1) 新規セッション作成
    r = session_requests.post(_url("/api/v1/sessions/create"), headers=auth_headers)
    assert r.status_code in (200, 201), r.text
    js = r.json()
    session_id = js["session_id"]

    # 2) 復元（空の会話でも appStatus など返る）
    r = session_requests.get(_url(f"/api/v1/sessions/restore/{session_id}"), headers=auth_headers)
    assert r.status_code == 200, r.text
    restored = r.json()
    assert restored.get("session_id") == session_id

    # 3) 曖昧質問を送信（今週末のおすすめなど）
    payload = {
        "session_id": session_id,
        "lang": "ja",
        "input_mode": "text",
        "message_text": "今週末、鳥海山周辺でのんびりできるおすすめスポットは？"
    }
    r = session_requests.post(_url("/api/v1/chat/message"), headers=auth_headers, json=payload)
    assert r.status_code in (200, 202), r.text
    js = r.json()
    task_id = js.get("task_id") or js.get("id") or js.get("taskId")
    assert task_id, f"no task id: {js}"

    # 4) ポーリングで結果取得
    result = wait_chat_result(session_requests, task_id)
    # 期待：最終応答が返る（ナッジ材料を元にした提案文）
    assert "final_response" in result
    assert isinstance(result["final_response"], str)
    assert len(result["final_response"]) > 0
