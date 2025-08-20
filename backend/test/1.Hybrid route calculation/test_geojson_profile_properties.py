# -*- coding: utf-8 -*-
import pytest
from sqlalchemy.orm import Session

# このテストは OSRM と DB が必要なため、既存マーカーを流用します
pytestmark = [pytest.mark.integration, pytest.mark.db, pytest.mark.osrm]

def _collect_profiles(fc):
    assert fc and fc.get("type") == "FeatureCollection", "geojson must be FeatureCollection"
    features = fc.get("features", [])
    assert isinstance(features, list) and len(features) > 0, "features must be non-empty list"

    profiles = []
    for i, f in enumerate(features):
        props = f.get("properties")
        assert isinstance(props, dict), f"Feature[{i}].properties must be a dict"
        p = props.get("profile")
        assert p in ("car", "foot"), f"Feature[{i}].properties.profile must be 'car' or 'foot' (got: {p})"
        profiles.append(p)
    return set(profiles)

def test_profile_set_on_direct_car_route(osrm_ready):
    if not osrm_ready:
        pytest.skip("OSRM is not reachable")

    from worker.app.services.routing.routing_service import RoutingService
    rs = RoutingService()

    # 近接2点（既存の test_osrm_client と同じレンジ）
    origin = (39.131, 140.069)
    dest   = (39.134, 140.072)

    r = rs.calculate_full_itinerary_route([origin, dest], profile="car")
    profs = _collect_profiles(r["geojson"])
    assert profs == {"car"}

def test_profile_set_on_direct_foot_route(osrm_ready):
    if not osrm_ready:
        pytest.skip("OSRM is not reachable")

    from worker.app.services.routing.routing_service import RoutingService
    rs = RoutingService()

    origin = (39.131, 140.069)
    dest   = (39.134, 140.072)

    r = rs.calculate_full_itinerary_route([origin, dest], profile="foot")
    profs = _collect_profiles(r["geojson"])
    assert profs == {"foot"}

def test_profile_set_on_hybrid_leg(db_session: Session, osrm_ready, any_access_point):
    if not osrm_ready:
        pytest.skip("OSRM is not reachable")
    if not any_access_point:
        pytest.skip("No access_points data")

    ap_id, ap_name, ap_type, ap_lat, ap_lon = any_access_point

    from worker.app.services.routing.routing_service import RoutingService
    rs = RoutingService()

    # AP付近を出発、目的地は「山岳」扱いでAP経由のハイブリッドを誘発
    origin = (ap_lat, ap_lon)
    dest   = (ap_lat + 0.01, ap_lon + 0.01)

    leg = rs.calculate_hybrid_leg(
        db_session,
        origin=origin,
        dest=dest,
        dest_spot_type="mountain",   # 直行不可の条件に合わせる
        dest_tags=None,
        ap_max_km=20.0,
    )
    profs = _collect_profiles(leg["geojson"])
    # ハイブリッドでは car/foot の両方が入っているはず
    assert profs == {"car", "foot"}
