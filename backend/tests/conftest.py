# -*- coding: utf-8 -*-
import random
import string
from typing import Dict, Tuple

import pytest
from fastapi.testclient import TestClient

# FastAPI アプリ本体をインポート
from api_gateway.app.main import app


@pytest.fixture(scope="session")
def client() -> TestClient:
    """
    TestClient をセッションスコープで共有。
    DB/Redis/Worker は docker compose 起動済み（モードA前提）。
    """
    return TestClient(app)


def _rand_suffix(n: int = 6) -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choice(chars) for _ in range(n))


def register_and_login(client: TestClient) -> Tuple[Dict, Dict]:
    """
    テストユーザーを登録→ログインして、(register_resp.json(), login_tokens) を返す。
    実装が username を受ける仕様に合わせる。
    """
    username = f"tester_{_rand_suffix()}@example.com"
    password = "Passw0rd!"
    display_name = "pytest user"

    # register（実装は id ではなく user_id を返す）
    r = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password, "display_name": display_name},
    )
    assert r.status_code in (200, 201), r.text
    reg = r.json()
    user_id = reg.get("user_id") or reg.get("id")
    assert user_id, f"register response unexpected: {reg}"

    # ✅ login は JSON ボディ必須に合わせる（フォームではなく JSON）
    r = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert r.status_code == 200, r.text
    tokens = r.json()
    assert tokens.get("access_token") and tokens.get("refresh_token"), tokens

    return reg, tokens


@pytest.fixture()
def token_pair(client: TestClient) -> Tuple[Dict, Dict]:
    """
    多くのテストで使う登録＋ログイン済みトークン。
    """
    return register_and_login(client)
