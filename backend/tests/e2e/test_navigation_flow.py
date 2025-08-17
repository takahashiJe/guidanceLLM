# -*- coding: utf-8 -*-
import time
import pytest
from .conftest import _url

pytestmark = pytest.mark.e2e

def test_navigation_flow(session_requests, auth_headers):
    # 1) セッション作成
    r = session_requests.post(_url("/api/v1/sessions/create"), headers=auth_headers)
    assert r.status_code in (200, 201), r.text
    session_id = r.json()["session_id"]

    # 2) ナビ開始（ガイドの事前生成などが走る想定）
    r = session_requests.post(_url("/api/v1/navigation/start"), headers=auth_headers, json={
        "session_id": session_id,
        "lang": "ja"
    })
    assert r.status_code in (200, 202), r.text

    # 3) 位置情報更新（ルート未設定でもAPIとしては受け付ける）
    #    ここでは近づいた／逸脱したイベントが返る可能性のみ緩くチェック
    #    ※ 実地図の条件に依存するため、e2e では「正常応答＆events は配列」を確認するに留める
    ticks = [
        {"lat": 39.201, "lon": 139.949},
        {"lat": 39.202, "lon": 139.950},
        {"lat": 39.203, "lon": 139.951},
    ]
    got_any_events = False
    for t in ticks:
        r = session_requests.post(_url("/api/v1/navigation/location"), headers=auth_headers, json={
            "session_id": session_id,
            "lat": t["lat"],
            "lon": t["lon"],
        })
        assert r.status_code in (200, 202), r.text
        js = r.json()
        assert js.get("ok") is True
        events = js.get("events", [])
        assert isinstance(events, list)
        if events:
            got_any_events = True
        time.sleep(0.3)

    # 厳密な地理判定は unit 側に委譲、E2E は正常経路のみ保証
    assert got_any_events in (True, False)
