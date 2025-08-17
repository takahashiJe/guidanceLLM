# -*- coding: utf-8 -*-
from fastapi.testclient import TestClient
from .conftest import register_and_login


def test_register_and_login_flow(client: TestClient):
    reg, tokens = register_and_login(client)
    assert reg.get("user_id") or reg.get("id")
    assert tokens.get("access_token")


def test_refresh_token_flow(client: TestClient, token_pair):
    _, tokens = token_pair
    refresh = tokens["refresh_token"]

    r = client.post("/api/v1/auth/token/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200, r.text
    new_tokens = r.json()
    assert new_tokens.get("access_token"), new_tokens
    assert new_tokens.get("refresh_token"), new_tokens
    # access が更新されていること（必須ではないが、念のため）
    assert new_tokens["access_token"] != tokens["access_token"]
