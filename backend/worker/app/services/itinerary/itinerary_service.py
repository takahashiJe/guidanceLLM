# /app/backend/worker/app/services/itinerary/itinerary_service.py

from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy import select

from worker.app.services.itinerary import crud_plan
from worker.app.services.routing.routing_service import RoutingService

from worker.app.services.routing.client import OSRMNoRouteError
from worker.app.services.routing.access_points_repo import find_nearest_access_point
from worker.app.services.routing.drive_rules import is_car_direct_accessible

from shared.app.models import Spot

# --- Public API for Orchestration Layer ---

def create_plan_for_user(
    db: Session, *, user_id: int, session_id: str, title: str, start_date: date
) -> Dict[str, Any]:
    """
    ユーザーのために新しい周遊計画を作成し、その詳細なサマリーを返す。
    """
    new_plan = crud_plan.create_new_plan(
        db, user_id=user_id, session_id=session_id, title=title, start_date=start_date
    )
    # 作成直後だが、将来的な拡張性のためサマリー関数経由で返す
    return summarize_plan(db, plan_id=new_plan.id)

def add_spot_to_user_plan(
    db: Session, *, plan_id: int, spot_id: int, position: Optional[int] = None
) -> Dict[str, Any]:
    """
    既存の計画にスポットを追加し、更新された計画のサマリーを返す。
    """
    crud_plan.add_spot_to_plan(db, plan_id=plan_id, spot_id=spot_id, position=position)
    return summarize_plan(db, plan_id=plan_id)


def remove_spot_from_user_plan(db: Session, *, plan_id: int, spot_id: int) -> Dict[str, Any]:
    """
    既存の計画からスポットを削除し、更新された計画のサマリーを返す。
    """
    crud_plan.remove_spot_from_plan(db, plan_id=plan_id, spot_id=spot_id)
    return summarize_plan(db, plan_id=plan_id)


def reorder_user_plan_stops(
    db: Session, *, plan_id: int, spot_ids_in_order: List[int]
) -> Dict[str, Any]:
    """
    計画の訪問順を並べ替え、更新された計画のサマリーを返す。
    """
    crud_plan.reorder_plan_stops(db, plan_id=plan_id, spot_ids_in_order=spot_ids_in_order)
    return summarize_plan(db, plan_id=plan_id)


def summarize_plan(db: Session, *, plan_id: int) -> Dict[str, Any]:
    plan_summary = crud_plan.summarize_plan_stops(db, plan_id=plan_id) or {}
    plan_summary.setdefault("route_geojson", None)
    plan_summary.setdefault("total_duration_minutes", 0)
    plan_summary.setdefault("legs", [])  # ← 追加（形を安定させる）

    stops = plan_summary.get("stops") or []
    if len(stops) < 2:
        return plan_summary

    spot_ids: List[int] = [int(s["spot_id"]) for s in stops if "spot_id" in s]

    rows = db.execute(
        select(Spot.id, Spot.latitude, Spot.longitude, Spot.spot_type, Spot.tags)
        .where(Spot.id.in_(spot_ids))
    ).all()

    meta = {int(r.id): {"lat": float(r.latitude), "lon": float(r.longitude),
                        "type": (r.spot_type or ""), "tags": r.tags} for r in rows}

    waypoints: List[Tuple[float, float]] = []
    kinds_tags: List[Tuple[str, Any]] = []
    used_spot_ids: List[int] = []   # ← 追加：実際に採用したspot_idを追跡
    for sid in spot_ids:
        m = meta.get(sid)
        if not m:
            continue
        waypoints.append((m["lat"], m["lon"]))
        kinds_tags.append((m["type"], m["tags"]))
        used_spot_ids.append(sid)  # ← 追加

    if len(waypoints) < 2:
        return plan_summary

    rs = RoutingService()
    features: List[Dict[str, Any]] = []
    legs: List[Dict[str, Any]] = []  # ← 追加：レグを貯める
    total_min: float = 0.0

    for i in range(len(waypoints) - 1):
        origin = waypoints[i]
        dest   = waypoints[i + 1]
        dest_type, dest_tags = kinds_tags[i + 1]

        leg = rs.calculate_hybrid_leg(
            db,
            origin=origin,
            dest=dest,
            dest_spot_type=dest_type,
            dest_tags=dest_tags,
            ap_max_km=20.0,
        )
        print(f"DEBUG: leg return value: {leg}")

        # まず legs に格納（GeoJSONの有無に関係なくカウントさせる）
        # distance_m = leg.get("distance_m") or leg.get("distance_meters")
        # distance_km = (distance_m / 1000.0) if distance_m is not None else None
        legs.append({
            "from_spot_id": used_spot_ids[i],
            "to_spot_id":   used_spot_ids[i + 1],
            "distance_km": leg.get("distance_km"),
            "duration_min": int(round(float(leg.get("duration_min", 0.0)))),
            "mode": leg.get("mode", "hybrid"),
            "used_ap": leg.get("used_ap")  # ← ここで追加
        })

        total_min += float(leg.get("duration_min", 0.0))

        # 既存のGeoJSON統合ロジックはそのまま
        gj = leg.get("geojson")
        if not gj:
            continue
        t = gj.get("type")
        if t == "Feature":
            features.append(gj)
        elif t == "FeatureCollection":
            features.extend(gj.get("features") or [])
        else:
            features.append({"type": "Feature", "properties": {}, "geometry": gj})

    plan_summary["legs"] = legs  # ← 追加：まとめて設定

    if features:
        plan_summary["route_geojson"] = {"type": "FeatureCollection", "features": features}
        plan_summary["total_duration_minutes"] = int(round(total_min))
    else:
        plan_summary["route_geojson"] = None
        plan_summary["total_duration_minutes"] = 0

    return plan_summary

# --- API for Information Service ---

# 混雑ステータスのしきい値
CONGESTION_THRESHOLDS = {"low_max": 10, "mid_max": 30}
MV_NAME = "congestion_by_date_spot"  # マテリアライズドビュー名

def compute_hybrid_polyline_from_origin(
    db: OrmSession,
    *,
    origin: Tuple[float, float],
    stops: List[Stop],
) -> Tuple[Dict[str, Any], float, float]:
    """
    [ADDED] 任意起点 origin から stops を順に辿るハイブリッド経路（car+foot）を算出。
      - 各 leg: P(i) -> P(i+1)
      - 車で到達できない場合は AccessPoint を自動選定して car→AP, AP→dest を連結
    返り値: (FeatureCollection, total_distance_m, total_duration_s)
    """
    fc: Dict[str, Any] = {"type": "FeatureCollection", "features": [], "properties": {}}
    total_dist_m: float = 0.0
    total_dur_s: float = 0.0

    routing = RoutingService()  # [KEPT] 既存のOSRMクライアント／タイムアウト等の設定を内部で持つ前提
    ap_repo = AccessPointsRepository(db)

    # P0 は origin、P1..Pn は stops のスポット座標
    prev_lat, prev_lon = origin

    for idx, stop in enumerate(stops):
        latlon = _as_point(stop)
        if not latlon or latlon[0] is None or latlon[1] is None:
            # Spot に座標がない場合はスキップ（未知データ）
            continue
        dest_lat, dest_lon = latlon

        if can_drive_to_spot(stop):
            # [KEPT] 既存ルール：車で直接行けるスポット
            feat, dist_m, dur_s = routing.route_car((prev_lat, prev_lon), (dest_lat, dest_lon))
            _append_feature(fc, feat)
            total_dist_m += dist_m or 0.0
            total_dur_s += dur_s or 0.0
        else:
            # [KEPT] 車では到達不可：最寄り AP を探索して car→AP, AP→dest(foot) で分割
            ap = ap_repo.find_nearest_access_point_for_spot(stop)
            if ap is None:
                # フォールバック：全足（foot）で直結（短距離向け）
                feat, dist_m, dur_s = routing.route_foot((prev_lat, prev_lon), (dest_lat, dest_lon))
                _append_feature(fc, feat)
                total_dist_m += dist_m or 0.0
                total_dur_s += dur_s or 0.0
            else:
                # car leg: prev -> AP
                feat1, dist1, dur1 = routing.route_car((prev_lat, prev_lon), (ap.latitude, ap.longitude))
                _append_feature(fc, feat1)
                total_dist_m += dist1 or 0.0
                total_dur_s += dur1 or 0.0

                # foot leg: AP -> dest
                feat2, dist2, dur2 = routing.route_foot((ap.latitude, ap.longitude), (dest_lat, dest_lon))
                _append_feature(fc, feat2)
                total_dist_m += dist2 or 0.0
                total_dur_s += dur2 or 0.0

        # 次 leg の出発点はこの目的地
        prev_lat, prev_lon = dest_lat, dest_lon

    # properties に合計値を格納
    fc["properties"]["distance_m"] = float(total_dist_m)
    fc["properties"]["duration_s"] = float(total_dur_s)

    return fc, total_dist_m, total_dur_s

def get_congestion_info(*, db: Session, spot_id: int, visit_date: date) -> Dict[str, Any]:
    """
    指定されたスポットと日付の混雑情報を取得する。
    (information_serviceから利用される)
    """
    count = _get_congestion_count(db, spot_id=spot_id, visit_date=visit_date)
    status = _get_congestion_status(count)
    return {"count": count, "status": status}


def _get_congestion_count(db: Session, *, spot_id: int, visit_date: date) -> int:
    """
    DBから混雑度（同日訪問予定のユーザー数）を取得する。
    まずマテリアライズドビューを参照し、失敗した場合は実テーブルをJOINして集計する。
    """
    # 1. マテリアライズドビューを参照
    try:
        count = db.execute(
            text(
                f'SELECT user_count FROM "{MV_NAME}" WHERE spot_id = :spot_id AND visit_date = :visit_date'
            ),
            {"spot_id": spot_id, "visit_date": visit_date},
        ).scalar_one_or_none()
        if count is not None:
            return int(count)
    except Exception:
        # MVが存在しないかリフレッシュされていない場合、フォールバック
        pass

    # 2. フォールバック: 実テーブルをJOINして集計
    count = db.execute(
        text(
            """
            SELECT COUNT(DISTINCT p.user_id) AS user_count
            FROM plans p
            JOIN stops s ON s.plan_id = p.id
            WHERE s.spot_id = :spot_id AND p.start_date = :visit_date
            """
        ),
        {"spot_id": spot_id, "visit_date": visit_date},
    ).scalar_one_or_none() or 0
    return int(count)


def _get_congestion_status(count: int) -> str:
    """
    人数から混雑ステータスのテキストを返す。
    """
    if count <= CONGESTION_THRESHOLDS["low_max"]:
        return "空いているでしょう"
    elif count <= CONGESTION_THRESHOLDS["mid_max"]:
        return "比較的穏やかでしょう"
    return "混雑が予想されます"

def _merge_feature_collections(collections: List[Dict[str, Any]]) -> Dict[str, Any]:
    """複数の FeatureCollection を単純結合"""
    merged = {"type": "FeatureCollection", "features": []}
    for col in collections:
        if not col:
            continue
        feats = col.get("features") or []
        merged["features"].extend(feats)
    return merged

def _as_point(stop: Stop) -> Tuple[float, float]:
    """[ADDED] Stop→(lat,lon)。Spot の緯度経度を利用。"""
    sp: Optional[Spot] = stop.spot
    return (sp.latitude, sp.longitude) if sp else (None, None)


def _append_feature(fc: Dict[str, Any], feat: Dict[str, Any]) -> None:
    fc.setdefault("type", "FeatureCollection")
    fc.setdefault("features", [])
    fc["features"].append(feat)