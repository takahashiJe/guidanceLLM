# worker/app/services/routing/routing_service.py

from typing import List, Dict, Any, Optional, Literal
import logging

# OSRMとの通信を担当するクライアントをインポート
from worker.app.services.routing.client import OSRMClient

# ロガーの設定
logger = logging.getLogger(__name__)

class RoutingService:
    """
    地理空間に関する全ての計算を専門に担うサービスクラス。
    OSRMClientを内部で利用し、他のサービスにはシンプルなインターフェースを提供する。
    """

    def __init__(self):
        """サービスの初期化。OSRMクライアントをインスタンス化する。"""
        self.client = OSRMClient()

    def calculate_full_itinerary_route(
        self,
        waypoints: List[Dict[str, float]],
        profile: Literal["car", "foot"],
        round_trip: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        [達成事項1] 周遊計画の全行程ルートを計算し、GeoJSONを返す。
        """
        # 入力値の検証
        if not waypoints or not isinstance(waypoints, list):
            logger.warning("calculate_full_itinerary_route received empty or invalid waypoints.")
            return None
        
        route_points = list(waypoints)
        if round_trip and len(route_points) > 1:
            route_points.append(route_points[0])

        try:
            route_data = self.client.fetch_route(route_points, profile)
            return route_data.get("geometry") if route_data else None
        except Exception as e:
            # client層で捕捉しきれなかった予期せぬエラーをここで捕捉
            logger.error(f"Unexpected error in calculate_full_itinerary_route: {e}", exc_info=True)
            return None

    def get_distance_and_duration(
        self,
        origin: Dict[str, float],
        destination: Dict[str, float],
        profile: Literal["car", "foot"]
    ) -> Optional[Dict[str, float]]:
        """
        [達成事項2] 2点間の距離と所要時間を計算する。
        """
        # 入力値の検証
        if not all(k in origin for k in ["latitude", "longitude"]) or \
           not all(k in destination for k in ["latitude", "longitude"]):
            logger.warning("get_distance_and_duration received invalid coordinates.")
            return None

        try:
            return self.client.fetch_distance_and_duration(origin, destination, profile)
        except Exception as e:
            logger.error(f"Unexpected error in get_distance_and_duration: {e}", exc_info=True)
            return None

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
