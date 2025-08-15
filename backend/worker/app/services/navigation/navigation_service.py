# backend/worker/app/services/navigation/navigation_service.py
# =========================================================
# 目的:
# - ナビゲーション中のイベント検知（逸脱/接近）を行い、
#   オーケストレーターへ通知すべきイベントだけを返す。
# - 実アクション（リルート計算/TTS再生等）は行わない。
#
# 提供メソッド:
# - check_for_deviation(current_location, current_route_geojson, threshold_m=None)
# - check_for_proximity(current_location, guide_spots, default_radius_m=None, already_triggered=None)
#
# 返却仕様:
# - 逸脱あり:
#   {"event": "REROUTE_REQUESTED",
#    "data": {
#        "current_location": {"lat": ..., "lon": ...},
#        "distance_to_route_m": 123.4
#    }}
# - 接近あり（複数回り得る）:
#   [{"event": "PROXIMITY_SPOT_ID",
#     "data": {
#        "spot_id": "...",
#        "distance_m": 87.6,
#        "current_location": {"lat": ..., "lon": ...}
#     }} , ...]
#
# 設計メモ:
# - 閾値は geospatial_utils.get_env_distance_thresholds() を既定で使用
# - ルートは GeoJSON LineString / MultiLineString のどちらにも対応
# - guide_spots は spot_type='tourist_spot' のみを想定（呼び出し側で絞り込み推奨）
# - 接近は同じスポットに対して重複通知しないための already_triggered セットを受け取り可能
# =========================================================

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union
import math

from worker.app.services.navigation.geospatial_utils import (
    point_to_linestring_distance_m,
    haversine_distance_m,
    get_env_distance_thresholds,
)

LatLon = Tuple[float, float]
GeoJSON = Dict[str, Any]


def _collect_linestring_coords(geojson: GeoJSON) -> List[List[LatLon]]:
    """
    GeoJSON の LineString / MultiLineString から、座標列の配列を返す。
    - 戻り値: [ [(lat,lon), ...], [(lat,lon), ...], ... ]
    """
    if not geojson or "type" not in geojson:
        return []

    gtype = geojson.get("type")
    if gtype == "Feature":
        return _collect_linestring_coords(geojson.get("geometry") or {})
    if gtype == "FeatureCollection":
        coords: List[List[LatLon]] = []
        for feat in geojson.get("features", []):
            coords.extend(_collect_linestring_coords(feat))
        return coords

    if gtype == "LineString":
        # GeoJSONは [lon, lat]、内部では (lat,lon) で統一
        coords_ll = geojson.get("coordinates", [])
        return [[(lat, lon) for lon, lat in coords_ll]]

    if gtype == "MultiLineString":
        multi = geojson.get("coordinates", [])
        return [[(lat, lon) for lon, lat in part] for part in multi]

    # それ以外は非対応
    return []


class NavigationService:
    """逸脱検知・接近検知の軽量ロジックを提供するサービス層。"""

    def __init__(
        self,
        deviation_threshold_m: Optional[float] = None,
        default_proximity_radius_m: Optional[float] = None,
    ) -> None:
        th = get_env_distance_thresholds()
        self.deviation_threshold_m = deviation_threshold_m or th["deviation_m"]
        self.default_proximity_radius_m = default_proximity_radius_m or th["proximity_m"]

    # -----------------------------------------------------
    # 逸脱検知: 現在地とルート最近傍点の距離が threshold を超えれば逸脱
    # -----------------------------------------------------
    def check_for_deviation(
        self,
        current_location: Dict[str, float],
        current_route_geojson: GeoJSON,
        threshold_m: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        :param current_location: {"lat": float, "lon": float}
        :param current_route_geojson: LineString/MultiLineString/Feature(...) などの GeoJSON
        :param threshold_m: 上書き用の判定閾値（未指定なら環境値）
        :return: 逸脱イベント or None
        """
        lat = float(current_location["lat"])
        lon = float(current_location["lon"])
        th = threshold_m if threshold_m is not None else self.deviation_threshold_m

        # ルート座標列を取得
        lines = _collect_linestring_coords(current_route_geojson)
        if not lines:
            return None  # ルートがない場合は何もしない

        # 複数の LineString があれば最短距離を採用
        min_dist = math.inf
        for line in lines:
            d = point_to_linestring_distance_m((lat, lon), line)
            if d < min_dist:
                min_dist = d

        if min_dist > th:
            return {
                "event": "REROUTE_REQUESTED",
                "data": {
                    "current_location": {"lat": lat, "lon": lon},
                    "distance_to_route_m": float(min_dist),
                },
            }
        return None

    # -----------------------------------------------------
    # 接近検知: tourist_spot のみ対象。半径に入ったらイベントを列挙
    # -----------------------------------------------------
    def check_for_proximity(
        self,
        current_location: Dict[str, float],
        guide_spots: Iterable[Dict[str, Any]],
        default_radius_m: Optional[float] = None,
        already_triggered: Optional[Set[Union[int, str]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        :param current_location: {"lat": float, "lon": float}
        :param guide_spots: [{ "spot_id": ID, "lat": float, "lon": float,
                               "spot_type": "tourist_spot", "radius_m": optional }, ...]
                           ※ 呼び出し側で spot_type='tourist_spot' のみ渡すのが理想
        :param default_radius_m: 既定半径（未指定なら環境値）
        :param already_triggered: 既にガイド済みの spot_id 集合（重複通知防止用）
        :return: 発火すべきイベントの配列
        """
        lat = float(current_location["lat"])
        lon = float(current_location["lon"])
        base_radius = default_radius_m if default_radius_m is not None else self.default_proximity_radius_m

        fired: List[Dict[str, Any]] = []
        seen: Set[Union[int, str]] = already_triggered or set()

        for s in guide_spots:
            spot_type = s.get("spot_type")
            if spot_type and spot_type != "tourist_spot":
                # 念のため防御。アプリ設計上は呼び出し前にフィルタ済みのはず。
                continue

            spot_id = s.get("spot_id")
            if spot_id in seen:
                # 重複防止
                continue

            try:
                s_lat = float(s["lat"])
                s_lon = float(s["lon"])
            except (KeyError, ValueError, TypeError):
                continue

            radius_m = float(s.get("radius_m", base_radius))
            dist = haversine_distance_m(lat, lon, s_lat, s_lon)
            if dist <= radius_m:
                fired.append(
                    {
                        "event": "PROXIMITY_SPOT_ID",
                        "data": {
                            "spot_id": spot_id,
                            "distance_m": float(dist),
                            "current_location": {"lat": lat, "lon": lon},
                        },
                    }
                )
                # 呼び出し側の集合も更新して欲しいケースが多いので、参照が来ていれば加える
                if already_triggered is not None:
                    already_triggered.add(spot_id)

        return fired
