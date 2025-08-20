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
from __future__ import annotations
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union
import math

from worker.app.services.navigation.geospatial_utils import (
    point_to_linestring_distance_m,
    haversine_distance_m,
    get_env_distance_thresholds,
)

from datetime import datetime, timezone

# [ADDED] 既存 import に無ければ追加してください
from sqlalchemy.orm import Session as OrmSession, joinedload

# [ADDED] 既存モデルの再利用
from shared.app.models import Session as DbSession, Plan, Stop

# [ADDED] ハイブリッド経路計算（任意起点）＆ 楽観ロック更新のCRUD
from worker.app.services.itinerary.itinerary_service import compute_hybrid_polyline_from_origin
from worker.app.services.itinerary.crud_plan import update_plan_route_with_version

LatLon = Tuple[float, float]
GeoJSON = Dict[str, Any]

def _utcnow() -> datetime:
    """[ADDED] tz-aware 現在時刻（楽観ロックの更新時刻に使用）"""
    return datetime.now(timezone.utc)

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

def reorder_from_target(stops: List[Stop], target_stop_id: Optional[int]) -> List[Stop]:
    """
    [ADDED] target_stop_id を起点に、そこで切って末尾までの残区間を返す。
    - target_stop_id が None、または見つからない場合は stops 全体を返す。
    - 空 or None に対しては空配列を返す。
    """
    if not stops:
        return []
    if target_stop_id is None:
        return list(stops)

    id_to_idx = {s.id: i for i, s in enumerate(stops)}
    if target_stop_id not in id_to_idx:
        return list(stops)

    start = id_to_idx[target_stop_id]
    return stops[start:]

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

def reroute(
    db: Session,
    *,
    session_id: str,
    origin_lat: float,
    origin_lon: float,
    target_stop_id: Optional[int],
    base_route_version: Optional[int],
) -> Dict[str, Any]:
    """
    [ADDED] 現在地を“仮想先頭”として差し込み、残区間に対してハイブリッド経路を再計算。
    計算結果は Plan.route_geojson を CAS（route_version の楽観ロック）で更新する。

    Returns:
        {
          "updated": bool,           # 反映できたか（CAS成功）
          "new_version": int|None,   # 更新後の route_version（CAS失敗時は現行版）
          "reason": str|None,        # 失敗理由（no_active_plan / no_stops / empty_rest 等）
        }
    """
    # 1) セッション → アクティブプラン取得
    sess: Optional[DbSession] = (
        db.query(DbSession)
        .filter(DbSession.id == session_id)
        .first()
    )
    if not sess or not sess.active_plan_id:
        return {"updated": False, "new_version": None, "reason": "no_active_plan"}

    # stops と spot を同時ロード（順序は relationship 定義の order_by に従う）
    plan: Optional[Plan] = (
        db.query(Plan)
        .options(joinedload(Plan.stops).joinedload(Stop.spot))
        .filter(Plan.id == sess.active_plan_id)
        .first()
    )
    if plan is None:
        return {"updated": False, "new_version": None, "reason": "plan_not_found"}

    if not plan.stops:
        return {"updated": False, "new_version": plan.route_version if hasattr(plan, "route_version") else None, "reason": "no_stops"}

    # 念のため Python 側でも order を安定化（order_index が無い場合は id で代用）
    try:
        plan.stops.sort(key=lambda s: getattr(s, "order_index"))
    except Exception:
        plan.stops.sort(key=lambda s: getattr(s, "id"))

    # 2) 残区間（target_stop_id 以降）を決める
    rest: List[Stop] = reorder_from_target(plan.stops, target_stop_id)
    if not rest:
        return {"updated": False, "new_version": plan.route_version, "reason": "empty_rest"}

    # 3) 任意起点（現在地）からハイブリッド経路を構築
    #    - 既存の Step1 実装に依存：車で到達不可のスポットは AP 自動選定して car→AP, AP→dest(foot)
    route_fc, total_dist_m, total_dur_s = compute_hybrid_polyline_from_origin(
        db,
        origin=(origin_lat, origin_lon),
        stops=rest,
    )

    # FeatureCollection.properties に合計距離/時間を格納（なければ）
    props = route_fc.get("properties") or {}
    if "distance_m" not in props and total_dist_m is not None:
        props["distance_m"] = float(total_dist_m)
    if "duration_s" not in props and total_dur_s is not None:
        props["duration_s"] = float(total_dur_s)
    route_fc["properties"] = props

    # 4) CAS（楽観ロック）で Plan を更新：WHERE route_version = base_route_version
    updated, new_version = update_plan_route_with_version(
        db,
        plan_id=plan.id,
        base_version=base_route_version,
        new_geojson=route_fc,
        updated_at=_utcnow(),
    )

    return {
        "updated": updated,
        "new_version": new_version,
        "reason": None if updated else "cas_conflict" if base_route_version is not None else "unknown",
    }