# worker/app/services/routing/routing_service.py

from typing import List, Dict, Any, Optional, Literal

from worker.app.services.routing.client import OSRMClient

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

        Args:
            waypoints (List[Dict[str, float]]): 訪問先の座標リスト。
            profile (Literal["car", "foot"]): 移動モード。
            round_trip (bool): Trueの場合、出発点に戻る往復ルートを計算する。

        Returns:
            Optional[Dict[str, Any]]: Leafletで描画可能なGeoJSONオブジェクト。
        """
        if not waypoints:
            return None
        
        route_points = list(waypoints)
        if round_trip and len(route_points) > 1:
            # [FR-5-2] 帰路の自動設定
            route_points.append(route_points[0])

        route_data = self.client.fetch_route(route_points, profile)
        return route_data.get("geometry") if route_data else None

    def get_distance_and_duration(
        self,
        origin: Dict[str, float],
        destination: Dict[str, float],
        profile: Literal["car", "foot"]
    ) -> Optional[Dict[str, float]]:
        """
        [達成事項2] 2点間の距離と所要時間を計算する。情報提供サービス部から同期的に呼び出される。

        Args:
            origin (Dict[str, float]): 出発地の座標。
            destination (Dict[str, float]): 目的地の座標。
            profile (Literal["car", "foot"]): 移動モード。

        Returns:
            Optional[Dict[str, float]]: 距離(km)と時間(分)の辞書。
        """
        return self.client.fetch_distance_and_duration(origin, destination, profile)

    def calculate_reroute(
        self,
        current_location: Dict[str, float],
        remaining_waypoints: List[Dict[str, float]],
        profile: Literal["car", "foot"]
    ) -> Optional[Dict[str, Any]]:
        """
        [達成事項3] ナビ中のリルート計算を行う。

        Args:
            current_location (Dict[str, float]): ユーザーの現在地座標。
            remaining_waypoints (List[Dict[str, float]]): 残りの訪問先座標リスト。
            profile (Literal["car", "foot"]): 移動モード。

        Returns:
            Optional[Dict[str, Any]]: 新しいルートのGeoJSONオブジェクト。
        """
        if not remaining_waypoints:
            return None
            
        # 現在地を新しい出発点としてルートを再計算
        route_points = [current_location] + remaining_waypoints
        
        route_data = self.client.fetch_route(route_points, profile)
        return route_data.get("geometry") if route_data else None