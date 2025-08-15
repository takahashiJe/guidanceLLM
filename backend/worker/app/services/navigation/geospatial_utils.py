# backend/worker/app/services/navigation/geospatial_utils.py
# =========================================================
# 目的:
# - 位置・距離計算のユーティリティを一箇所に集約
# - SRID（WGS84/緯度経度）前提の軽量な計算を提供
# - 依存を極力避け、CPU でも高速に動く実装
#
# 主な提供関数:
# - haversine_distance_m(lat1, lon1, lat2, lon2): 2点間の直線距離（メートル）
# - point_to_linestring_distance_m(point, linestring): 点とポリラインの最短距離（メートル）
# - get_env_distance_thresholds(): env から逸脱/接近パラメータを読み出し
#
# 設計メモ:
# - 直線距離はハバースイン（球面三角法）
# - 点-線分距離は Web メルカトル相当の簡易投影（局所近似）での
#   2D 距離計算（緯度に応じて X のスケーリングのみを調整）
# - 日本国内の観光用途かつ「閾値 50〜200m 程度」の判定なので十分な精度
# =========================================================

import math
import os
from typing import Iterable, Tuple, Dict, Any

EARTH_RADIUS_M = 6371000.0  # 地球半径（メートル / WGS84 想定）


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """ハバースインで 2点間の直線距離（メートル）を返す。"""
    rlat1 = math.radians(lat1)
    rlat2 = math.radians(lat2)
    dlat = rlat2 - rlat1
    dlon = math.radians(lon2 - lon1)

    a = math.sin(dlat / 2.0) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_M * c


def _latlon_to_local_xy_m(lat: float, lon: float, lat0: float) -> Tuple[float, float]:
    """
    緯度 lat0 を基準に、lat/lon をローカル平面（メートル）に近似変換する。
    - X は経度方向。cos(lat0) でスケールする。
    - Y は緯度方向。
    """
    # 1度あたりの距離（おおよそ）
    m_per_deg_lat = 111_320.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))
    x = (lon) * m_per_deg_lon
    y = (lat) * m_per_deg_lat
    return x, y


def _point_segment_distance_xy(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    """2D 平面上の点と線分 ab の最短距離（メートル）を返す。"""
    vx = bx - ax
    vy = by - ay
    wx = px - ax
    wy = py - ay

    seg_len2 = vx * vx + vy * vy
    if seg_len2 == 0.0:
        # a==b の退化ケースは点距離
        dx = px - ax
        dy = py - ay
        return math.hypot(dx, dy)

    # 射影係数 t を [0,1] にクランプ
    t = max(0.0, min(1.0, (wx * vx + wy * vy) / seg_len2))
    proj_x = ax + t * vx
    proj_y = ay + t * vy
    return math.hypot(px - proj_x, py - proj_y)


def point_to_linestring_distance_m(
    point: Tuple[float, float],
    linestring: Iterable[Tuple[float, float]],
) -> float:
    """
    緯度経度の点 `point=(lat,lon)` と、ポリライン `linestring=[(lat,lon), ...]`
    の最短距離（メートル）を返す。
    """
    lat_p, lon_p = point
    coords = list(linestring)
    if len(coords) < 2:
        # 線分なし → 代表点との距離
        if len(coords) == 1:
            lat0, lon0 = coords[0]
            return haversine_distance_m(lat_p, lon_p, lat0, lon0)
        return float("inf")

    # ローカル平面化の基準緯度は点の緯度に合わせる
    lat0 = lat_p
    px, py = _latlon_to_local_xy_m(lat_p, lon_p, lat0)

    # 各線分で最小距離を探索
    min_d = float("inf")
    # あらかじめ線の各点もローカル平面に
    xy = [_latlon_to_local_xy_m(lat, lon, lat0) for (lat, lon) in coords]
    for i in range(len(xy) - 1):
        ax, ay = xy[i]
        bx, by = xy[i + 1]
        d = _point_segment_distance_xy(px, py, ax, ay, bx, by)
        if d < min_d:
            min_d = d
    return min_d


def get_env_distance_thresholds() -> Dict[str, Any]:
    """
    .env から閾値を取得する。未設定の場合はデフォルト値を採用。
    - NAV_DEVIATION_THRESHOLD_M: ルート逸脱の判定距離（m）
    - NAV_PROXIMITY_RADIUS_M:    スポット接近の基本半径（m）
    """
    deviation = float(os.getenv("NAV_DEVIATION_THRESHOLD_M", "50"))
    proximity = float(os.getenv("NAV_PROXIMITY_RADIUS_M", "200"))
    return {
        "deviation_m": deviation,
        "proximity_m": proximity,
    }
