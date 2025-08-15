# backend/worker/app/services/routing/routing_service.py
# OSRMクライアントをラップし、公開インターフェースを提供（FR-5）
from __future__ import annotations
from typing import List, Dict, Any, Tuple, Optional

# 既存の client.py（OSRMClient）を利用する前提
from worker.app.services.routing.client import OSRMClient

class RoutingService:
    """OSRM クライアントの薄いファサード"""

    def __init__(self, osrm_car_base: str = "http://osrm-car:5000", osrm_foot_base: str = "http://osrm-foot:5000"):
        self.client = OSRMClient(osrm_car_base=osrm_car_base, osrm_foot_base=osrm_foot_base)

    def calculate_full_itinerary_route(self, waypoints: List[Tuple[float, float]], profile: str = "car", roundtrip: bool = False) -> Dict[str, Any]:
        """
        :param waypoints: [(lon, lat), ...] の配列（OSRM 準拠）
        :param profile: "car" or "foot"
        :param roundtrip: True の場合は最終地点に出発点を追加（FR-5-2）
        """
        if roundtrip and waypoints and waypoints[0] != waypoints[-1]:
            waypoints = list(waypoints) + [waypoints[0]]
        return self.client.fetch_route(waypoints=waypoints, profile=profile)

    def get_distance_and_duration(self, origin: Tuple[float, float], destination: Tuple[float, float], profile: str = "car") -> Dict[str, float]:
        """ナッジ用の軽量距離/時間取得（km / 分）"""
        res = self.client.fetch_distance_and_duration(origin=origin, destination=destination, profile=profile)
        return {
            "distance_km": float(res.get("distance_km", 0.0)),
            "duration_min": float(res.get("duration_min", 0.0)),
        }

    def calculate_reroute(self, current_location: Tuple[float, float], remaining_waypoints: List[Tuple[float, float]], profile: str = "car") -> Dict[str, Any]:
        """リルート用。現在地 + 残りウェイポイントで新ルートを生成"""
        waypoints = [current_location] + list(remaining_waypoints)
        return self.client.fetch_route(waypoints=waypoints, profile=profile)
