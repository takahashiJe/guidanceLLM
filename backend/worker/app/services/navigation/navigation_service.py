# worker/app/services/navigation/navigation_service.py

from typing import List, Dict, Any, Optional

# 地理空間計算のためのユーティリティ関数をインポート
from worker.app.services.navigation import geospatial_utils

class NavigationService:
    """
    ナビゲーション中のリアルタイムイベント処理を担うサービスクラス。
    ユーザーの現在地を受け取り、ルート逸脱やスポットへの接近を検知する。
    このクラスのインスタンスは、ナビゲーションセッションごとに生成・維持されることを想定。
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
        if not route_geojson or not route_geojson.get("coordinates"):
            raise ValueError("Route GeoJSON is invalid or missing coordinates.")
            
        self.route_geojson = route_geojson
        self.guide_spots = guide_spots
        self.deviation_threshold = deviation_threshold_meters
        # 一度のナビゲーションセッションで、同じガイドが何度も再生されるのを防ぐためのセット
        self.triggered_spot_ids = set()

    def update_user_location(self, current_location: Dict[str, float]) -> Optional[Dict[str, Any]]:
        """
        ユーザーの現在地を更新し、発生したイベントを返す。
        このメソッドが、外部から定期的に呼び出されるメインの処理となる。

        Args:
            current_location (Dict[str, float]): ユーザーの最新の座標。 {"latitude": ..., "longitude": ...}

        Returns:
            Optional[Dict[str, Any]]: 検知したイベント。なければNone。
                - ルート逸脱時: {"event_type": "reroute_request"}
                - スポット接近時: {"event_type": "proximity_alert", "spot_id": "spot_xxx"}
        """
        if not all(k in current_location for k in ["latitude", "longitude"]):
            print("Error: Invalid current_location format.")
            return None

        # [達成事項1: FR-5-3] ルート逸脱検知
        # geospatial_utilsを使い、現在地とルートの最短距離を計算
        deviation_distance = geospatial_utils.calculate_distance_from_route(
            current_location, self.route_geojson
        )
        
        # 閾値を超えていたら、リルート要求イベントを返す
        if deviation_distance > self.deviation_threshold:
            print(f"Deviation detected! Distance: {deviation_distance:.2f}m. Threshold: {self.deviation_threshold}m")
            return {"event_type": "reroute_request"}

        # [達成事項2: FR-5-4] スポットへの接近検知
        for spot in self.guide_spots:
            spot_id = spot.get("spot_id")
            # spot_idがない、または既にこのセッションでガイド済みの場合はスキップ
            if not spot_id or spot_id in self.triggered_spot_ids:
                continue

            try:
                spot_location = {"latitude": spot["latitude"], "longitude": spot["longitude"]}
                trigger_radius = spot["trigger_radius_meters"]

                # geospatial_utilsを使い、現在地がスポットのトリガー半径内に入ったか判定
                if geospatial_utils.is_within_radius(current_location, spot_location, trigger_radius):
                    print(f"Proximity alert for spot: {spot_id}")
                    # このスポットを「ガイド済み」として記録
                    self.triggered_spot_ids.add(spot_id)
                    # スポット接近イベントを返す
                    return {"event_type": "proximity_alert", "spot_id": spot_id}
            except (KeyError, TypeError) as e:
                # guide_spotsのデータ構造が不正な場合に備える
                print(f"Error processing spot {spot_id}: {e}")
                continue
        
        # イベントが発生しなかった場合はNoneを返す
        return None
