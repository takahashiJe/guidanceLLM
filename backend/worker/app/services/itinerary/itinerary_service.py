# /app/backend/worker/app/services/itinerary/itinerary_service.py

from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from worker.app.services.itinerary import crud_plan
from worker.app.services.routing.routing_service import calculate_full_itinerary_route

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
    ルート計算サービスとの連携もここで行う。
    """
    # 1. DBから訪問地の基本情報を取得
    plan_summary = crud_plan.summarize_plan_stops(db, plan_id=plan_id)

    if not plan_summary or not plan_summary.get("stops"):
        plan_summary["route_geojson"] = None
        plan_summary["total_duration_minutes"] = 0
        return plan_summary

    # 2. 訪問地が2か所以上ある場合、ルート計算サービスを呼び出す
    if len(plan_summary["stops"]) >= 2:
        try:
            # routing_serviceはspot_idのリストを要求する
            spot_ids = [stop["spot_id"] for stop in plan_summary["stops"]]
            route_info = calculate_full_itinerary_route(db, spot_ids)
            plan_summary["route_geojson"] = route_info["geojson"]
            plan_summary["total_duration_minutes"] = route_info["total_duration_minutes"]
        except Exception as e:
            # ルート計算に失敗しても処理を続行し、情報はNoneとする
            print(f"Error calculating route for plan_id={plan_id}: {e}")
            plan_summary["route_geojson"] = None
            plan_summary["total_duration_minutes"] = 0
    else:
        plan_summary["route_geojson"] = None
        plan_summary["total_duration_minutes"] = 0

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