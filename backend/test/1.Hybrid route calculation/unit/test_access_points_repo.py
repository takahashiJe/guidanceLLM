# -*- coding: utf-8 -*-
import math
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from worker.app.services.routing.access_points_repo import find_nearest_access_point

pytestmark = pytest.mark.db

def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2-lat1)
    dlon = math.radians(lon2-lon1)
    a = (math.sin(dlat/2)**2 +
         math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2)
    return 2*R*math.asin(math.sqrt(a))

def test_find_nearest_matches_sql_knn(db_session: Session):
    # 代表点(経度緯度) : access_points から1点選び、そこを基準に検証
    seed = db_session.execute(
        text("SELECT latitude, longitude FROM access_points WHERE ap_type IN ('parking','trailhead') ORDER BY id LIMIT 1")
    ).first()
    if not seed:
        pytest.skip("No access_points data")
    lat, lon = float(seed[0]), float(seed[1])

    ap = find_nearest_access_point(db_session, lat=lat, lon=lon, max_km=50.0)
    assert ap is not None
    ap_id, name, ap_type, ap_lat, ap_lon = ap
    # KNNで同一点なら距離はほぼ0のはず
    assert _haversine_km(lat, lon, ap_lat, ap_lon) < 0.05  # 50m 以内
    assert ap_type in ("parking", "trailhead")
