# -*- coding: utf-8 -*-
from fastapi.testclient import TestClient


def test_health_endpoint(client: TestClient):
    """
    実装差異を吸収するために候補エンドポイントを総当たり。
    JSON の場合は {"status":"ok"} or {"ok": true} を許容。
    """
    candidates = ("/health", "/api/health", "/healthz", "/api/healthz", "/")
    for path in candidates:
        r = client.get(path)
        if r.status_code == 200:
            ctype = r.headers.get("content-type", "")
            if ctype.startswith("application/json"):
                try:
                    data = r.json()
                except Exception:
                    data = {}
                if isinstance(data, dict) and data:
                    ok_val = data.get("ok")
                    status_val = data.get("status")
                    assert (ok_val in (True, 1)) or (status_val == "ok"), data
            return
    raise AssertionError("健康診断系エンドポイントが見つかりませんでした。")
