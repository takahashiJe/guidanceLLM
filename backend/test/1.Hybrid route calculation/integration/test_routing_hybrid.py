# -*- coding: utf-8 -*-
import pytest
from sqlalchemy.orm import Session

pytestmark = [pytest.mark.integration, pytest.mark.db, pytest.mark.osrm]

def test_hybrid_leg_uses_access_point(db_session: Session, osrm_ready, any_access_point):
    if not osrm_ready:
        pytest.skip("OSRM is not reachable")
    if not any_access_point:
        pytest.skip("No access_points data")

    from worker.app.services.routing.routing_service import RoutingService
    # drive_rules を差し替える必要がある実装なら monkeypatch で強制
    # 今回は calculate_hybrid_leg 側が is_car_direct_accessible を内部で呼ぶ想定:
    # 目的地を 'mountain' として渡し、car直行不可を誘発
    ap_id, ap_name, ap_type, ap_lat, ap_lon = any_access_point

    # 出発地: AP から少し離れた点
    origin = (ap_lat + 0.01, ap_lon + 0.01)
    dest   = (ap_lat, ap_lon)  # 目的地はAPぴったりにして検証しやすく

    rs = RoutingService()
    # calculate_hybrid_leg が routing_service にある前提
    leg = rs.calculate_hybrid_leg(
        db_session,
        origin=origin,
        dest=dest,
        dest_spot_type="mountain",   # ← 直行不可の条件に合うように
        dest_tags=None,
        ap_max_km=20.0,
    )

    assert "geojson" in leg and "distance_km" in leg and "duration_min" in leg
    assert isinstance(leg.get("used_ap"), (dict, type(None)))
    # AP 経由であれば used_ap が入るはず
    assert leg["used_ap"] is not None
    assert leg["used_ap"]["name"] == ap_name
