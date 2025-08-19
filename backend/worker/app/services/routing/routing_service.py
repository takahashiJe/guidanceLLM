# -*- coding: utf-8 -*-
"""
Routing Service（公開インターフェース）
- オーケストレーターや情報提供サービス部から呼ばれ、
  OSRMClient を用いて距離/時間や GeoJSON を返す。
- ビジネス判断（どの profile を使うか等）はオーケストレーター側の責務。
"""

from __future__ import annotations

from typing import List, Tuple, Dict, Any

from worker.app.services.routing.client import OSRMClient, OSRMNoRouteError, OSRMProfile

from worker.app.services.routing.access_points_repo import find_nearest_access_point
from worker.app.services.routing.drive_rules import is_car_direct_accessible

def _to_tuple(lat: float, lon: float) -> Tuple[float, float]:
    """(lat, lon) -> tuple"""
    return (lat, lon)


class RoutingService:
    """OSRMClient を内部に抱える薄いファサード"""

    def __init__(self) -> None:
        self.client = OSRMClient()

    # ================
    # 軽量: 距離/時間
    # ================
    def get_distance_and_duration(
        self, origin: tuple[float, float], destination: tuple[float, float], profile: OSRMProfile
    ) -> dict:
        """
        入出力は (lat,lon) のタプル。
        戻り値: {"distance_km": float, "duration_min": float}
        """
        distance_km, duration_min = self.client.fetch_distance_and_duration(origin, destination, profile)
        return {"distance_km": distance_km, "duration_min": duration_min}

    # =========================
    # 重量: ルート全体（GeoJSON）
    # =========================
    def calculate_full_itinerary_route(
        self, waypoints: List[tuple[float, float]], profile: OSRMProfile, piston: bool = False
    ) -> dict:
        """
        入力: waypoints=[(lat,lon), ...], profile="car"/"foot", piston=True/False
        戻り値: {"geojson": <FeatureCollection>, "distance_km": float, "duration_min": float}
        """
        if len(waypoints) < 2:
            raise ValueError("waypoints は 2 箇所以上が必要です。")

        geojson, distance_km, duration_min = self.client.fetch_route(waypoints, profile, piston=piston)
        return {"geojson": geojson, "distance_km": distance_km, "duration_min": duration_min}
    
    def calculate_hybrid_leg(
        self,
        db,
        *,
        origin: tuple[float, float],
        dest: tuple[float, float],
        dest_spot_type: str | None,
        dest_tags: dict | None,
        ap_max_km: float = 20.0,
        piston: bool = False,
    ) -> Dict[str, Any]:
        """
        origin→dest の1区間をハイブリッド計算:
          - 目的地が車直行可なら car で1本
          - 目的地が直行不可なら: 最近傍APを取り car(origin→AP) + foot(AP→dest)
        戻り値: {
          "geojson": FeatureCollection,
          "distance_km": float,
          "duration_min": float,
          "used_ap": {"name":..,"type":..,"latitude":..,"longitude":..} or None
        }
        """
        car_ok = is_car_direct_accessible(dest_spot_type, dest_tags)

        # 1) 直行可: car をまず試し、ダメなら foot にフォールバック
        if car_ok:
            try:
                return self.calculate_full_itinerary_route([origin, dest], profile="car", piston=piston)
            except OSRMNoRouteError:
                return self.calculate_full_itinerary_route([origin, dest], profile="foot", piston=piston)

        # 2) 直行不可: 目的地の近傍APを取得（駐車場/登山口）
        ap = find_nearest_access_point(db, lat=dest[0], lon=dest[1], max_km=ap_max_km)
        if not ap:
            # AP不在 → 最後の手段で car, それもダメなら foot
            try:
                return self.calculate_full_itinerary_route([origin, dest], profile="car", piston=piston)
            except OSRMNoRouteError:
                return self.calculate_full_itinerary_route([origin, dest], profile="foot", piston=piston)

        _, ap_name, ap_type, ap_lat, ap_lon = ap
        ap_pt = (ap_lat, ap_lon)

        # car: origin→AP
        car_seg = self.calculate_full_itinerary_route([origin, ap_pt], profile="car", piston=piston)
        # foot: AP→dest
        foot_seg = self.calculate_full_itinerary_route([ap_pt, dest], profile="foot", piston=piston)

        # 結合・集計
        merged = self._merge_features([car_seg["geojson"], foot_seg["geojson"]])
        return {
            "geojson": merged,
            "distance_km": float(car_seg["distance_km"]) + float(foot_seg["distance_km"]),
            "duration_min": float(car_seg["duration_min"]) + float(foot_seg["duration_min"]),
            "used_ap": {"name": ap_name, "type": ap_type, "latitude": ap_lat, "longitude": ap_lon},
        }

    @staticmethod
    def _merge_features(collections: list[dict]) -> dict:
        """
        GeoJSON FeatureCollection を単純連結。
        OSRM のコレクションは features[] を持つので、それを順番に足す。
        """
        merged = {"type": "FeatureCollection", "features": []}
        for col in collections:
            if not col:
                continue
            feats = col.get("features") or []
            merged["features"].extend(feats)
        return merged
        
    # ===================================
    # 将来用: 現在地からのリルート（薄いラッパ）
    # ===================================
    def calculate_reroute(
        self, current_location: tuple[float, float], remaining_waypoints: List[tuple[float, float]], profile: OSRMProfile
    ) -> dict:
        """
        入力: 現在地 + 残りの経由地 -> 新ルート
        戻り値は calculate_full_itinerary_route と同形式。
        """
        waypoints = [current_location, *remaining_waypoints]
        return self.calculate_full_itinerary_route(waypoints, profile, piston=False)

