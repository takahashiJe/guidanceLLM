# -*- coding: utf-8 -*-
import pytest
from datetime import date
from sqlalchemy import text
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.integration, pytest.mark.db, pytest.mark.osrm]

def test_itinerary_summarize_hybrid_path(monkeypatch, db_session: Session, osrm_ready):
    if not osrm_ready:
        pytest.skip("OSRM is not reachable")

    # --- スポットを2件確保 ---
    rows = db_session.execute(
        text("SELECT id FROM spots ORDER BY id ASC LIMIT 2")
    ).fetchall()
    if len(rows) < 2:
        pytest.skip("Need at least 2 spots loaded")

    spot_a = int(rows[0][0])
    spot_b = int(rows[1][0])

    # --- 目的地の座標を AP 近傍に上書き（竜ヶ原湿原） ---
    dest_lat, dest_lon = 39.1303513, 140.0660516
    db_session.execute(
        text("DELETE FROM spots WHERE latitude = :lat AND longitude = :lon AND id != :sid"),
        {"lat": dest_lat, "lon": dest_lon, "sid": spot_b},
    )
    db_session.execute(
        text("UPDATE spots SET latitude = :lat, longitude = :lon WHERE id = :sid"),
        {"lat": dest_lat, "lon": dest_lon, "sid": spot_b},
    )
    db_session.commit()

    # --- セッションを1件用意（user_id は NULL でOK） ---
    db_session.execute(
        text(
            """
            INSERT INTO sessions (id, created_at, updated_at)
            VALUES (:sid, now(), now())
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"sid": "test-e2e"},
    )

    # --- 目的地は「直行不可」にしてAP経由を強制（テストの安定化） ---
    from worker.app.services.routing import drive_rules
    monkeypatch.setattr(drive_rules, "is_car_direct_accessible", lambda st, tags: False)

    # --- 行程を作成（必須引数つき） ---
    from worker.app.services.itinerary import crud_plan
    from worker.app.services.itinerary import itinerary_service

    created = crud_plan.create_new_plan(
        db_session,
        user_id=None,
        session_id="test-e2e",
        start_date=date.today(),
    )
    plan_id = getattr(created, "id", created)

    # 2スポットを追加
    crud_plan.add_spot_to_plan(db_session, plan_id=plan_id, spot_id=spot_a)
    crud_plan.add_spot_to_plan(db_session, plan_id=plan_id, spot_id=spot_b)

    # --- summarize 実行 ---
    summary = itinerary_service.summarize_plan(db_session, plan_id=plan_id)

    # --- 検証 ---
    assert "route_geojson" in summary
    assert summary["route_geojson"] and summary["route_geojson"]["type"] == "FeatureCollection"
    assert "legs" in summary and len(summary["legs"]) == 1  # 2点なのでレグは1つ
    leg = summary["legs"][0]
    assert leg["distance_km"] > 0
    assert leg["duration_min"] > 0
    assert leg.get("used_ap") is not None
