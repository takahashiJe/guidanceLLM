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
    """
    指定された計画の現在の状態（訪問地リスト、ルート情報など）を要約して返す。
    ハイブリッド経路（車+徒歩+AP）をレグごとに組み立てる。
    """
    plan_summary = crud_plan.summarize_plan_stops(db, plan_id=plan_id) or {}
    # デフォルト値
    plan_summary.setdefault("route_geojson", None)
    plan_summary.setdefault("total_duration_minutes", 0)

    stops = plan_summary.get("stops") or []
    if len(stops) < 2:
        return plan_summary

    # 使うのは spot_id だけに依存させる（座標や種別はここで引く）
    spot_ids: List[int] = [int(s["spot_id"]) for s in stops if "spot_id" in s]

    # Spot 情報をまとめて取得
    rows = db.execute(
        select(
            Spot.id, Spot.latitude, Spot.longitude, Spot.spot_type, Spot.tags
        ).where(Spot.id.in_(spot_ids))
    ).all()

    meta = {
        int(r.id): {
            "lat": float(r.latitude),
            "lon": float(r.longitude),
            "type": (r.spot_type or ""),
            "tags": r.tags,
        }
        for r in rows
    }

    # 並び順にウェイポイントと種別/タグを整列
    waypoints: List[Tuple[float, float]] = []
    kinds_tags: List[Tuple[str, Any]] = []
    for sid in spot_ids:
        m = meta.get(sid)
        if not m:
            # 片方でも欠けるとレグが作れないので素通り
            continue
        waypoints.append((m["lat"], m["lon"]))
        kinds_tags.append((m["type"], m["tags"]))

    if len(waypoints) < 2:
        return plan_summary

    rs = RoutingService()
    features: List[Dict[str, Any]] = []
    total_min: float = 0.0

    legs: List[Dict[str, Any]] = []
    # 隣接ペアごとにハイブリッドルートを取得して積み上げ
    for i in range(len(waypoints) - 1):
        origin = waypoints[i]
        dest = waypoints[i + 1]
        dest_type, dest_tags = kinds_tags[i + 1]

        leg = rs.calculate_hybrid_leg(
            db,
            origin=origin,
            dest=dest,
            dest_spot_type=dest_type,
            dest_tags=dest_tags,
            ap_max_km=20.0,   # 必要なら設定値に
        )
        legs.append(leg)  # ここでlegsに追加

        total_min += float(leg.get("duration_min", 0.0))

        gj = leg.get("geojson")
        if not gj:
            # このレグだけスキップ（全体は返す）
            continue

        # Feature / FeatureCollection を吸収して 1本の FC にする
        t = gj.get("type")
        if t == "Feature":
            features.append(gj)
        elif t == "FeatureCollection":
            features.extend(gj.get("features") or [])
        else:
            # geometry だけなら Feature に包む
            features.append({"type": "Feature", "properties": {}, "geometry": gj})

    if features:
        plan_summary["route_geojson"] = {
            "type": "FeatureCollection",
            "features": features,
        }
        plan_summary["total_duration_minutes"] = int(round(total_min))
    else:
        plan_summary["route_geojson"] = None
        plan_summary["total_duration_minutes"] = 0

    plan_summary["legs"] = legs
    return plan_summary


# --- API for Information Service ---

# 混雑ステータスのしきい値
CONGESTION_THRESHOLDS = {"low_max": 10, "mid_max": 30}
MV_NAME = "congestion_by_date_spot"  # マテリアライズドビュー名


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