# -*- coding: utf-8 -*-
import os
import time
import uuid
import pytest
import requests

API_BASE = os.getenv("E2E_API_BASE", "http://localhost:8000")
RUN_E2E = os.getenv("E2E_RUN", "0") == "1"

pytestmark = pytest.mark.skipif(not RUN_E2E, reason="Set E2E_RUN=1 to enable E2E tests.")

def _url(path: str) -> str:
    path = path if path.startswith("/") else f"/{path}"
    return f"{API_BASE}{path}"

@pytest.fixture(scope="session")
def api_base() -> str:
    return API_BASE

@pytest.fixture(scope="session")
def session_requests():
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    return s

@pytest.fixture(scope="session")
def health_ok(session_requests, api_base):
    r = session_requests.get(_url("/health"))
    assert r.status_code == 200
    js = r.json()
    assert js.get("status") == "ok"
    return True

@pytest.fixture(scope="session")
def test_user_credentials():
    # テスト用ユーザー（毎回ユニークなメールでもOK）
    suffix = uuid.uuid4().hex[:8]
    email = f"e2e_{suffix}@example.com"
    password = "Password123!"
    return {"email": email, "password": password}

@pytest.fixture(scope="session")
def auth_tokens(session_requests, test_user_credentials, health_ok):
    # register → 既に存在なら login → login
    email = test_user_credentials["email"]
    password = test_user_credentials["password"]

    # register
    r = session_requests.post(_url("/api/v1/auth/register"), json={"email": email, "password": password})
    if r.status_code not in (200, 201, 400, 409):
        pytest.fail(f"unexpected register status: {r.status_code}, {r.text}")

    # login
    r = session_requests.post(_url("/api/v1/auth/login"), data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    js = r.json()
    assert "access_token" in js and "refresh_token" in js
    return {"access": js["access_token"], "refresh": js["refresh_token"], "email": email}

@pytest.fixture()
def auth_headers(auth_tokens):
    return {"Authorization": f"Bearer {auth_tokens['access']}"}

def wait_chat_result(session_requests, task_id: str, timeout_sec: int = 45, interval: float = 0.5):
    """
    chat/message の task_id を受けて、結果が出るまでポーリングする共通ユーティリティ。
    期待エンドポイント: GET /api/v1/chat/result/{task_id}
    """
    deadline = time.time() + timeout_sec
    last_body = None
    while time.time() < deadline:
        res = session_requests.get(_url(f"/api/v1/chat/result/{task_id}"))
        if res.status_code == 200:
            body = res.json()
            last_body = body
            status = body.get("status") or body.get("state") or body.get("task_status")
            if status in ("SUCCESS", "done", "ok", "READY"):
                # result があれば返却
                return body.get("result") or body
            if status in ("FAILURE", "ERROR"):
                pytest.fail(f"chat task failure: {body}")
        time.sleep(interval)
    pytest.fail(f"chat result timeout. last={last_body}")
