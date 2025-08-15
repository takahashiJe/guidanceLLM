# -*- coding: utf-8 -*-
"""
OSRM 専用クライアント
- 車用(osrm-car) / 徒歩用(osrm-foot) へ HTTP で接続してルート情報を取得する。
- 低レベルな HTTP 通信（URL 構築 / リトライ / タイムアウト / 例外変換）を担う。
"""

from __future__ import annotations

import os
import time
from typing import Iterable, List, Literal, Tuple

import requests


OSRMProfile = Literal["car", "foot"]


class OSRMClientError(Exception):
    """OSRM 通信時の一般的なエラー"""


class OSRMNoRouteError(OSRMClientError):
    """ルートが見つからない場合のエラー"""


def _coords_to_path(coords: Iterable[Tuple[float, float]]) -> str:
    """
    OSRM の /route/v1/{profile}/{lon,lat;lon,lat...} 形式の座標部分を生成する。
    引数は (lat, lon) のタプル列を受け取り、(lon,lat) に入れ替えて組み立てる。
    """
    parts = []
    for lat, lon in coords:
        parts.append(f"{lon:.7f},{lat:.7f}")
    return ";".join(parts)


class OSRMClient:
    """
    OSRM サーバーへの HTTP 通信をカプセル化したクライアント。
    - profile='car' -> OSRM_CAR_HOST
    - profile='foot' -> OSRM_FOOT_HOST
    """

    def __init__(self, timeout_sec: float = 10.0, max_retries: int = 2, backoff_sec: float = 1.0) -> None:
        self.car_base = os.getenv("OSRM_CAR_HOST", "http://osrm-car:5000")
        self.foot_base = os.getenv("OSRM_FOOT_HOST", "http://osrm-foot:5000")
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        self.backoff_sec = backoff_sec

    # =========================
    # 内部: ルートAPI 呼び出し
    # =========================
    def _route_request(
        self, profile: OSRMProfile, coords: List[Tuple[float, float]]
    ) -> dict:
        """
        OSRM /route エンドポイントを叩き、JSON を返す。
        - steps: false（軽量化）
        - overview: full（Leaflet で描画しやすい）
        - geometries: geojson
        - annotations: distance,duration（集約のため）
        """
        if profile == "car":
            base = self.car_base
            osrm_profile = "driving"
        else:
            base = self.foot_base
            osrm_profile = "foot"

        path = _coords_to_path(coords)  # (lat,lon) -> "lon,lat;lon,lat"
        url = f"{base}/route/v1/{osrm_profile}/{path}"
        params = {
            "geometries": "geojson",
            "overview": "full",
            "steps": "false",
            "annotations": "distance,duration",
        }

        # 簡易リトライ（タイムアウト・一時的エラー対策）
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.get(url, params=params, timeout=self.timeout_sec)
                if resp.status_code >= 500:
                    # サーバー側一時障害はリトライ
                    raise OSRMClientError(f"OSRM 5xx: {resp.status_code} {resp.text}")
                resp.raise_for_status()
                data = resp.json()
                return data
            except (requests.Timeout, requests.ConnectionError, OSRMClientError) as e:
                last_exc = e
                if attempt < self.max_retries:
                    time.sleep(self.backoff_sec * (attempt + 1))
                    continue
                break
            except requests.HTTPError as e:
                # 4xx はそのまま失敗（リトライしない）
                last_exc = e
                break

        raise OSRMClientError(f"OSRM request failed: {last_exc}")

    # =========================
    # 公開: 2点間の距離/時間
    # =========================
    def fetch_distance_and_duration(
        self,
        origin: Tuple[float, float],
        destination: Tuple[float, float],
        profile: OSRMProfile,
    ) -> Tuple[float, float]:
        """
        2点間の距離(km) と 所要時間(分) を返す。
        """
        data = self._route_request(profile, [origin, destination])
        routes = data.get("routes") or []
        if not routes:
            raise OSRMNoRouteError("No route found")

        route = routes[0]
        distance_m = float(route.get("distance", 0.0))
        duration_s = float(route.get("duration", 0.0))
        distance_km = distance_m / 1000.0
        duration_min = duration_s / 60.0
        return distance_km, duration_min

    # =========================
    # 公開: 経路全体（GeoJSON）
    # =========================
    def fetch_route(
        self,
        waypoints: List[Tuple[float, float]],
        profile: OSRMProfile,
        piston: bool = False,
    ) -> Tuple[dict, float, float]:
        """
        経由地を含む全行程の GeoJSON と 集約距離/時間 を返す。
        piston=True の場合、ピストン（出発点へ戻る）にするため先頭座標を終端に追加する。
        戻り値: (geojson, distance_km, duration_min)
        """
        if piston and len(waypoints) >= 2:
            waypoints = [*waypoints, waypoints[0]]

        data = self._route_request(profile, waypoints)
        routes = data.get("routes") or []
        if not routes:
            raise OSRMNoRouteError("No route found")

        route = routes[0]
        distance_km = float(route.get("distance", 0.0)) / 1000.0
        duration_min = float(route.get("duration", 0.0)) / 60.0
        geometry = route.get("geometry", {})

        # Leaflet ですぐ描画できるよう FeatureCollection に整形
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "distance_km": distance_km,
                        "duration_min": duration_min,
                        "profile": profile,
                    },
                    "geometry": geometry,
                }
            ],
        }
        return geojson, distance_km, duration_min
