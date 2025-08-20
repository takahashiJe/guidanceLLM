# -*- coding: utf-8 -*-
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.osrm]

def test_osrm_direct_route(osrm_ready):
    if not osrm_ready:
        pytest.skip("OSRM is not reachable")

    # RoutingService 経由でテスト（client直叩きでもOK）
    from worker.app.services.routing.routing_service import RoutingService

    rs = RoutingService()
    # 適当な近接2点（鳥海山域のAPが既にDBに入っている前提で、その近辺の座標）
    origin = (39.131, 140.069)
    dest   = (39.134, 140.072)

    # driving
    r1 = rs.calculate_full_itinerary_route([origin, dest], profile="car")
    assert r1["distance_km"] > 0
    assert r1["duration_min"] > 0
    assert r1["geojson"]["type"] == "FeatureCollection"

    # foot
    r2 = rs.calculate_full_itinerary_route([origin, dest], profile="foot")
    assert r2["distance_km"] > 0
    assert r2["duration_min"] > 0
    assert r2["geojson"]["type"] == "FeatureCollection"
