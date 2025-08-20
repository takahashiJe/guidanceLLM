# backend/shared/app/services/navigation_events.py
# [NEW] API/Worker共通：現在地に対するイベント判定の純関数と、幾何ユーティリティを集約。

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Any
import math

from shared.app.models import Plan, Stop

EARTH_RADIUS_M = 6371000.0


@dataclass(frozen=True)
class Thresholds:
    off_route_m: float
    approach_m: float
    arrival_m: float


def haversine_m(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    s = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(s))


def _project_local_m(lat0: float, lon0: float, lat: float, lon: float) -> tuple[float, float]:
    x = math.radians(lon - lon0) * EARTH_RADIUS_M * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * EARTH_RADIUS_M
    return x, y


def _point_segment_distance_m(lat: float, lon: float, a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat0 = (a[0] + b[0]) / 2.0
    x1, y1 = _project_local_m(lat0, lon, a[0], a[1])
    x2, y2 = _project_local_m(lat0, lon, b[0], b[1])
    xp, yp = _project_local_m(lat0, lon, lat, lon)
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(xp - x1, yp - y1)
    t = ((xp - x1) * dx + (yp - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    xn = x1 + t * dx
    yn = y1 + t * dy
    return math.hypot(xp - xn, yp - yn)


def distance_to_polyline_m(point: Tuple[float, float], route_geojson: Optional[dict]) -> Optional[float]:
    if not route_geojson:
        return None
    lat, lon = point
    features = route_geojson.get("features") or []
    best = None
    for f in features:
        geom = f.get("geometry") or {}
        if geom.get("type") != "LineString":
            continue
        coords = geom.get("coordinates") or []
        for i in range(len(coords) - 1):
            lon1, lat1 = coords[i][0], coords[i][1]
            lon2, lat2 = coords[i + 1][0], coords[i + 1][1]
            d = _point_segment_distance_m(lat, lon, (lat1, lon1), (lat2, lon2))
            if best is None or (d is not None and d < best):
                best = d
    return best


def _find_next_stop(plan: Plan) -> Optional[Stop]:
    # [KEPT] 既存の order_index による昇順が定義されている前提で、先頭を next とする。
    if not plan.stops:
        return None
    return plan.stops[0]


def evaluate_events(
    current: Tuple[float, float],
    plan: Plan,
    thresholds: Thresholds,
) -> tuple[list[dict], Optional[Stop], Optional[float]]:
    """
    [ADDED] 現在地に対するイベント判定の純関数。
      - 逸脱（REROUTE_REQUESTED）
      - 接近（PROXIMITY_APPROACH）
      - 到着（PROXIMITY_ARRIVAL）
    戻り値: (events, next_stop, offroute_distance_m)
    """
    events: List[Dict[str, Any]] = []
    next_stop = _find_next_stop(plan)

    # 逸脱判定（ルートが未設定なら None）
    offroute = distance_to_polyline_m(current, plan.route_geojson)
    if offroute is None or offroute > thresholds.off_route_m:
        events.append(
            {
                "type": "REROUTE_REQUESTED",
                "reason": "off_route",
                "distance_to_route_m": int(offroute) if offroute is not None else -1,
            }
        )

    # 接近/到着
    if next_stop and next_stop.spot:
        d = haversine_m(current, (next_stop.spot.latitude, next_stop.spot.longitude))
        if d < thresholds.arrival_m:
            events.append({"type": "PROXIMITY_ARRIVAL", "stop_id": next_stop.id, "distance_m": int(d)})
        elif d < thresholds.approach_m:
            events.append({"type": "PROXIMITY_APPROACH", "stop_id": next_stop.id, "distance_m": int(d)})

    return events, next_stop, offroute
