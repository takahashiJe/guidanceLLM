# -*- coding: utf-8 -*-
"""
Routing Service（公開インターフェース）
- オーケストレーターや情報提供サービス部から呼ばれ、
  OSRMClient を用いて距離/時間や GeoJSON を返す。
- ビジネス判断（どの profile を使うか等）はオーケストレーター側の責務。
"""

from __future__ import annotations

from typing import List, Tuple

from worker.app.services.routing.client import OSRMClient, OSRMNoRouteError, OSRMProfile


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

