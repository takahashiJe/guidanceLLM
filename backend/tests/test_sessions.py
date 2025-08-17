# -*- coding: utf-8 -*-
from fastapi.testclient import TestClient


def test_sessions_create_and_restore(client: TestClient, token_pair):
    _, tokens = token_pair
    at = tokens["access_token"]
    headers = {"Authorization": f"Bearer {at}"}

    # ✅ /sessions/create は「body必須」になっている実装に合わせて空JSONを送る
    r = client.post("/api/v1/sessions/create", headers=headers, json={})
    assert r.status_code in (200, 201), r.text
    body = r.json()
    session_id = body.get("session_id")
    assert session_id, body

    # restore
    r = client.get(f"/api/v1/sessions/restore/{session_id}", headers=headers)
    assert r.status_code == 200, r.text
    state = r.json()

    # appStatus / active_plan_id / 履歴（10件以内）などを確認
    assert "appStatus" in state, state
    assert "active_plan_id" in state, state
    assert "history" in state and isinstance(state["history"], list), state
    assert len(state["history"]) <= 10

    # SYSTEM_TRIGGER 形式の履歴があれば形式チェック
    for msg in state["history"]:
        if isinstance(msg, str) and msg.startswith("[SYSTEM_TRIGGER:"):
            assert "]" in msg
