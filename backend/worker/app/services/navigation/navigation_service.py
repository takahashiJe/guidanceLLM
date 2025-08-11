# worker/app/services/navigation/navigation_service.py

from typing import List, Dict, Any, Optional

from worker.app.services.navigation import geospatial_utils

class NavigationService:
    """
    ナビゲーション中のリアルタイムイベント処理を担うサービスクラス。
    ユーザーの現在地を受け取り、ルート逸脱やスポットへの接近を検知する。
    """

    def __init__(
        self,
        route_geojson: Dict[str, Any],
        guide_spots: List[Dict[str, Any]],
        deviation_threshold_meters: float = 50.0
    ):
        """
        ナビゲーションセッションを初期化する。

        Args:
            route_geojson (Dict[str, Any]): 現在走行中のルートのGeoJSONデータ。
            guide_spots (List[Dict[str, Any]]): 案内対象スポットのリスト。
                各要素は {"spot_id": str, "latitude": float, "longitude": float, "trigger_radius_meters": float} の形式。
            deviation_threshold_meters (float): ルート逸脱と判断する距離の閾値。
        """
        self.route_geojson = route_geojson
        self.guide_spots = guide_spots
        self.deviation_threshold = deviation_threshold_meters
        self.triggered_spot_ids = set() # ガイドを一度再生したスポットを記録

    def update_user_location(self, current_location: Dict[str, float]) -> Optional[Dict[str, Any]]:
        """
        ユーザーの現在地を更新し、発生したイベントを返す。
        このメソッドが、外部から定期的に呼び出されるメインの処理となる。

        Args:
            current_location (Dict[str, float]): ユーザーの最新の座標。 {"latitude": ..., "longitude": ...}

        Returns:
            Optional[Dict[str, Any]]: 検知したイベント。なければNone。
                例: {"event_type": "reroute_request"}
                例: {"event_type": "proximity_alert", "spot_id": "spot_001"}
        """
        
        # 1. ルート逸脱検知
        deviation_distance = geospatial_utils.calculate_distance_from_route(
            current_location, self.route_geojson
        )
        if deviation_distance > self.deviation_threshold:
            print(f"Deviation detected! Distance: {deviation_distance:.2f}m")
            return {"event_type": "reroute_request"}

        # 2. スポットへの接近検知
        for spot in self.guide_spots:
            spot_id = spot.get("spot_id")
            if not spot_id or spot_id in self.triggered_spot_ids:
                continue # IDがないか、既にトリガー済みの場合はスキップ

            spot_location = {"latitude": spot["latitude"], "longitude": spot["longitude"]}
            trigger_radius = spot["trigger_radius_meters"]

            if geospatial_utils.is_within_radius(current_location, spot_location, trigger_radius):
                print(f"Proximity alert for spot: {spot_id}")
                self.triggered_spot_ids.add(spot_id) # このセッションでは再通知しない
                return {"event_type": "proximity_alert", "spot_id": spot_id}
        
        # イベントが発生しなかった場合
        return None