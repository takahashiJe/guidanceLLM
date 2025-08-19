# /app/backend/worker/app/services/itinerary/itinerary_service.py

from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from worker.app.services.itinerary import crud_plan
from worker.app.services.routing.routing_service import RoutingService

from worker.app.services.routing.client import OSRMNoRouteError
from worker.app.services.routing.access_points_repo import find_nearest_access_point
from worker.app.services.routing.drive_rules import is_car_direct_accessible

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
    ここで“ハイブリッド経路（car＋foot）”をレグ単位で構築する。
    """
    # --- Stop一覧（順序付き）を取得（List[Stop]）
    stops = crud_plan.summarize_plan_stops(db, plan_id=plan_id)

    # --- 出力用の stops 配列（UI/LLM向けの軽量辞書）を組み立て
    stops_out: List[Dict[str, Any]] = []
    for st in stops:
        sp = getattr(st, "spot", None)
        if not sp:
            # 万一リレーションが無い/参照切れならスキップ
            continue

        # position or order_index のどちらでも拾えるように
        idx = getattr(st, "position", None)
        if idx is None:
            idx = getattr(st, "order_index", None)

        stops_out.append(
            {
                "stop_id": st.id,
                "spot_id": sp.id,
                "index": idx,
                "official_name": getattr(sp, "official_name", None),
                "spot_type": getattr(sp, "spot_type", None),
                "tags": getattr(sp, "tags", None),
                "latitude": float(getattr(sp, "latitude", 0.0)),
                "longitude": float(getattr(sp, "longitude", 0.0)),
            }
        )

    # --- 訪問地が2未満ならルート無しで返す
    if len(stops_out) < 2:
        return {
            "plan_id": plan_id,
            "stops": stops_out,
            "route_geojson": None,
            "total_duration_minutes": 0.0,
            "total_distance_km": 0.0,
            "legs": [],
        }

    rs = RoutingService()
    legs: List[Dict[str, Any]] = []
    total_km = 0.0
    total_min = 0.0

    # --- 連続する Stop 間を 1 レグとして、ハイブリッドで構築
    for i in range(len(stops_out) - 1):
        a = stops_out[i]
        b = stops_out[i + 1]

        origin = (a["latitude"], a["longitude"])
        dest = (b["latitude"], b["longitude"])
        dest_spot_type = b.get("spot_type")
        dest_tags = b.get("tags")

        # 目的地が車で直行可能か？
        car_ok = is_car_direct_accessible(dest_spot_type, dest_tags)

        try:
            if car_ok:
                # 直行可 → car 一本（ダメなら foot フォールバック）
                leg = rs.calculate_full_itinerary_route([origin, dest], profile="car")
            else:
                # 直行不可 → 最近傍 AP（駐車場/登山口）を探索
                ap = find_nearest_access_point(db, lat=dest[0], lon=dest[1], max_km=20.0)
                if ap:
                    _, ap_name, ap_type, ap_lat, ap_lon = ap
                    ap_pt = (ap_lat, ap_lon)

                    car_seg = rs.calculate_full_itinerary_route([origin, ap_pt], profile="car")
                    foot_seg = rs.calculate_full_itinerary_route([ap_pt, dest], profile="foot")

                    merged = _merge_feature_collections([car_seg["geojson"], foot_seg["geojson"]])
                    leg = {
                        "geojson": merged,
                        "distance_km": float(car_seg["distance_km"]) + float(foot_seg["distance_km"]),
                        "duration_min": float(car_seg["duration_min"]) + float(foot_seg["duration_min"]),
                        "used_ap": {
                            "name": ap_name,
                            "type": ap_type,
                            "latitude": ap_lat,
                            "longitude": ap_lon,
                        },
                    }
                else:
                    # APが見つからない → car 試行（ダメなら foot）
                    leg = rs.calculate_full_itinerary_route([origin, dest], profile="car")

        except OSRMNoRouteError:
            # car が失敗した等 → foot で最後のフォールバック
            leg = rs.calculate_full_itinerary_route([origin, dest], profile="foot")

        # legs 集計
        legs.append(leg)
        total_km += float(leg["distance_km"])
        total_min += float(leg["duration_min"])

    # --- ルート（GeoJSON）全結合
    route_geojson = _merge_feature_collections([leg["geojson"] for leg in legs])

    return {
        "plan_id": plan_id,
        "stops": stops_out,
        "route_geojson": route_geojson,
        "total_duration_minutes": total_min,
        "total_distance_km": total_km,
        "legs": legs,  # used_ap が入るのでデバッグ/LLM提示に便利
    }


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