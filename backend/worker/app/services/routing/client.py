# worker/app/services/routing/client.py

import requests
from typing import List, Dict, Any, Optional, Literal
import logging

# ロガーの設定
logger = logging.getLogger(__name__)

class OSRMClient:
    """
    OSRMサーバーとのHTTP通信に特化したクライアントクラス。
    """

    def __init__(self):
        """
        クライアントを初期化し、各プロファイルのベースURLを設定する。
        """
        self.base_urls = {
            "car": "http://osrm-car:5000",
            "foot": "http://osrm-foot:5000"
        }

    def _format_coordinates(self, coordinates: List[Dict[str, float]]) -> str:
        """座標リストをOSRM APIが要求する "lon,lat;lon,lat" 形式の文字列に変換する。"""
        # 座標データが不正な場合にKeyErrorが発生しないようにgetを使用
        return ";".join([f"{coord.get('longitude', 0)},{coord.get('latitude', 0)}" for coord in coordinates])

    def fetch_route(
        self,
        coordinates: List[Dict[str, float]],
        profile: Literal["car", "foot"]
    ) -> Optional[Dict[str, Any]]:
        """
        複数の経由地を含むルート情報をOSRMから取得する。
        """
        if len(coordinates) < 2:
            return None 

        base_url = self.base_urls.get(profile)
        if not base_url:
            # 不正なprofileが指定された場合はエラーログを残し、Noneを返す
            logger.error(f"Invalid profile specified: {profile}")
            return None
            
        coords_str = self._format_coordinates(coordinates)
        # OSRM v5 APIの正しいエンドポイント形式に修正
        url = f"{base_url}/route/v1/{'driving' if profile == 'car' else 'foot'}/{coords_str}?overview=full&geometries=geojson"

        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status() 
            data = response.json()
            
            if data.get("code") == "Ok" and data.get("routes"):
                return data["routes"][0]
            else:
                # OSRMが計算失敗のメッセージを返した場合
                logger.warning(f"OSRM API returned an error for URL {url}: {data.get('message')}")
                return None
        except requests.exceptions.Timeout:
            logger.error(f"Timeout error when fetching route from OSRM URL: {url}")
            return None
        except requests.exceptions.RequestException as e:
            # ネットワークエラーやOSRMコンテナがダウンしている場合
            logger.error(f"RequestException when fetching route from OSRM URL {url}: {e}", exc_info=True)
            return None
        except Exception as e:
            # JSONデコードエラーなど、その他の予期せぬエラー
            logger.error(f"An unexpected error occurred in fetch_route for URL {url}: {e}", exc_info=True)
            return None

    def fetch_distance_and_duration(
        self,
        origin: Dict[str, float],
        destination: Dict[str, float],
        profile: Literal["car", "foot"]
    ) -> Optional[Dict[str, float]]:
        """
        2点間の距離(distance)と所要時間(duration)を取得する。
        """
        route_data = self.fetch_route([origin, destination], profile)
        if route_data:
            # レスポンスにキーが存在しない場合でもエラーにならないようにgetを使用
            distance_meters = route_data.get("distance", 0.0)
            duration_seconds = route_data.get("duration", 0.0)
            return {
                "distance_km": round(distance_meters / 1000, 1),
                "duration_min": round(duration_seconds / 60, 1)
            }
        return None
