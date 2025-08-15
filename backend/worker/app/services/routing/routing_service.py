# -*- coding: utf-8 -*-
"""
Routing Service（同期向け公開インターフェース）。
ここでは FR-3-3 で使われる get_distance_and_duration のみ使用。
OSRM 連携は client に隠蔽されている想定。
"""

from typing import List, Tuple
from worker.app.services.routing.client import OSRMClient


class RoutingService:
    def __init__(self):
        self.client = OSRMClient()

    def get_distance_and_duration(
        self,
        *,
        origin: Tuple[float, float],
        destination: Tuple[float, float],
        profile: str = "car",
    ) -> Tuple[float, float]:
        """
        2点間の距離[km]・時間[分]を返す。OSRM の最短経路の先頭ルートを採用。
        """
        distance_km, duration_min = self.client.fetch_distance_and_duration(
            origin, destination, mode="car" if profile == "car" else "foot"
        )
        return distance_km, duration_min

    def calculate_reroute(
        self,
        current_location: Dict[str, float],
        remaining_waypoints: List[Dict[str, float]],
        profile: Literal["car", "foot"]
    ) -> Optional[Dict[str, Any]]:
        """
        [達成事項3] ナビ中のリルート計算を行う。
        """
        # 入力値の検証
        if not remaining_waypoints or not isinstance(remaining_waypoints, list) or \
           not all(k in current_location for k in ["latitude", "longitude"]):
            logger.warning("calculate_reroute received invalid arguments.")
            return None
            
        route_points = [current_location] + remaining_waypoints
        
        try:
            route_data = self.client.fetch_route(route_points, profile)
            return route_data.get("geometry") if route_data else None
        except Exception as e:
            logger.error(f"Unexpected error in calculate_reroute: {e}", exc_info=True)
            return None
