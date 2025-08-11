# worker/app/services/routing/client.py

import requests
from typing import List, Dict, Any, Optional, Literal

class OSRMClient:
    """
    OSRMサーバーとのHTTP通信に特化したクライアントクラス。
    車用と徒歩用のエンジンを透過的に切り替え、リクエストの構築と
    レスポンスのパース、エラーハンドリングを行う。
    """

    def __init__(self):
        """
        クライアントを初期化し、各プロファイルのベースURLを設定する。
        Docker Composeのサービス名でコンテナにアクセスする。
        """
        self.base_urls = {
            "car": "http://osrm-car:5000",
            "foot": "http://osrm-foot:5000"
        }

    def _format_coordinates(self, coordinates: List[Dict[str, float]]) -> str:
        """座標リストをOSRM APIが要求する "lon,lat;lon,lat" 形式の文字列に変換する。"""
        return ";".join([f"{coord['longitude']},{coord['latitude']}" for coord in coordinates])

    def fetch_route(
        self,
        coordinates: List[Dict[str, float]],
        profile: Literal["car", "foot"]
    ) -> Optional[Dict[str, Any]]:
        """
        複数の経由地を含むルート情報をOSRMから取得する。

        Args:
            coordinates (List[Dict[str, float]]): 経由地の座標リスト。
            profile (Literal["car", "foot"]): 使用する移動プロファイル。

        Returns:
            Optional[Dict[str, Any]]: OSRMから返されたGeoJSONを含むルート情報。エラー時はNone。
        """
        if len(coordinates) < 2:
            return None # 2点未満ではルートを計算できない

        base_url = self.base_urls.get(profile)
        if not base_url:
            raise ValueError(f"Invalid profile specified: {profile}")
            
        coords_str = self._format_coordinates(coordinates)
        # overview=full: 詳細なジオメトリを取得 / geometries=geojson: GeoJSON形式で取得
        url = f"{base_url}/route/v1/driving/{coords_str}?overview=full&geometries=geojson"

        try:
            response = requests.get(url, timeout=20) # 複数地点の計算は時間がかかる可能性
            response.raise_for_status() # 200番台以外のステータスコードで例外を発生
            data = response.json()
            if data.get("code") == "Ok" and data.get("routes"):
                return data["routes"][0] # 最も一般的なルートを返す
            else:
                print(f"OSRM API returned an error: {data.get('message')}")
                return None
        except requests.RequestException as e:
            print(f"Error fetching route from OSRM ({profile}): {e}")
            return None

    def fetch_distance_and_duration(
        self,
        origin: Dict[str, float],
        destination: Dict[str, float],
        profile: Literal["car", "foot"]
    ) -> Optional[Dict[str, float]]:
        """
        2点間の距離(distance)と所要時間(duration)を取得する。

        Args:
            origin (Dict[str, float]): 出発地の座標。
            destination (Dict[str, float]): 目的地の座標。
            profile (Literal["car", "foot"]): 使用する移動プロファイル。

        Returns:
            Optional[Dict[str, float]]: 距離(km)と時間(分)。例: {"distance_km": 25.5, "duration_min": 45.1}
        """
        route_data = self.fetch_route([origin, destination], profile)
        if route_data:
            distance_meters = route_data.get("distance", 0)
            duration_seconds = route_data.get("duration", 0)
            return {
                "distance_km": round(distance_meters / 1000, 1),
                "duration_min": round(duration_seconds / 60, 1)
            }
        return None