# -*- coding: utf-8 -*-
import pytest
from typing import Dict, Any
from sqlalchemy.orm import Session

pytestmark = pytest.mark.db  # DBは読むだけ（APは使わないがSessionはfixtureで供給）

def test_hybrid_leg_composes_car_and_foot(monkeypatch, db_session: Session):
    # --- arrange: ダミーAPを返す
    from worker.app.services.routing import routing_service
    monkeypatch.setattr(
        routing_service,
        "find_nearest_access_point",
        lambda db, lat, lon, max_km=20.0: (999, "DUMMY-AP", "parking", lat, lon),
    )

    # --- arrange: 車直行不可にする
    from worker.app.services.routing import drive_rules
    monkeypatch.setattr(
        drive_rules,
        "is_car_direct_accessible",
        lambda spot_type, tags: False,
    )

    # --- arrange: OSRM を叩かずに決め打ちの値を返す
    from worker.app.services.routing.routing_service import RoutingService

    def fake_route(self, coords, profile="car", piston=False) -> Dict[str, Any]:
        if profile == "car":
            return {
                "geojson": {"type": "FeatureCollection", "features": [{"id": "car"}]},
                "distance_km": 10.0,
                "duration_min": 15.0,
            }
        else:
            return {
                "geojson": {"type": "FeatureCollection", "features": [{"id": "foot"}]},
                "distance_km": 2.0,
                "duration_min": 30.0,
            }

    monkeypatch.setattr(RoutingService, "calculate_full_itinerary_route", fake_route, raising=True)

    rs = RoutingService()
    origin = (39.0, 140.0)
    dest   = (39.01, 140.01)

    # --- act
    leg = rs.calculate_hybrid_leg(
        db_session,
        origin=origin,
        dest=dest,
        dest_spot_type="mountain",
        dest_tags={"foo": "bar"},
        ap_max_km=20.0,
    )

    # --- assert: car(10km/15min) + foot(2km/30min) の合算
    assert leg["distance_km"] == 12.0
    assert leg["duration_min"] == 45.0
    assert leg["geojson"]["type"] == "FeatureCollection"
    features = leg["geojson"]["features"]
    assert any(f.get("id") == "car" for f in features)
    assert any(f.get("id") == "foot" for f in features)
    assert leg["used_ap"]["name"] == "DUMMY-AP"
