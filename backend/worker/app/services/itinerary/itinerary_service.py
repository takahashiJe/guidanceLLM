# -*- coding: utf-8 -*-
"""
Itinerary Service（業務ロジック層）
- CRUD呼び出しのファサード
- 混雑集計（MV優先→フォールバックJOIN）
- スレッドプールでも安全な短時間トランザクションを意識
"""

from typing import Dict, List, Optional
from datetime import date

from sqlalchemy import select, func, text, and_
from sqlalchemy.orm import Session

from shared.app.database import SessionLocal
from shared.app.models import Plan, Stop, Spot
from .crud_plan import (
    create_new_plan as crud_create_new_plan,
    add_spot_to_plan as crud_add_spot_to_plan,
    remove_spot_from_plan as crud_remove_spot_from_plan,
    reorder_plan_stops as crud_reorder_plan_stops,
    summarize_plan_stops as crud_summarize_plan_stops,
)

# 混雑ステータスのしきい値（要件で提示の区分）
CONGESTION_THRESHOLDS = {
    "low_max": 10,    # 0-10
    "mid_max": 30,    # 11-30
    # 31+ は high
}

MV_NAME = "congestion_by_date_spot"  # マテリアライズドビュー名


def _get_db() -> Session:
    return SessionLocal()


def create_new_plan(user_id: int, session_id: str, start_date: date) -> Dict:
    with _get_db() as db:
        plan = crud_create_new_plan(db, user_id=user_id, session_id=session_id, start_date=start_date)
        return {"plan_id": plan.id, "start_date": str(plan.start_date)}


def add_spot(plan_id: int, spot_id: int, position: Optional[int] = None) -> Dict:
    with _get_db() as db:
        st = crud_add_spot_to_plan(db, plan_id=plan_id, spot_id=spot_id, position=position)
        return {"stop_id": st.id, "position": st.position, "spot_id": st.spot_id}


def remove_spot(plan_id: int, stop_id: int) -> Dict:
    with _get_db() as db:
        crud_remove_spot_from_plan(db, plan_id=plan_id, stop_id=stop_id)
        return {"ok": True}


def reorder(plan_id: int, new_order_stop_ids: List[int]) -> Dict:
    with _get_db() as db:
        crud_reorder_plan_stops(db, plan_id=plan_id, new_order_stop_ids=new_order_stop_ids)
        return {"ok": True}


def get_plan_summary(plan_id: int) -> Dict:
    """
    LLMに要約してもらうための材料を返す。
    """
    with _get_db() as db:
        stops = crud_summarize_plan_stops(db, plan_id=plan_id)
        items = []
        for s in stops:
            spot = db.get(Spot, s.spot_id)
            items.append(
                {
                    "stop_id": s.id,
                    "position": s.position,
                    "spot_id": s.spot_id,
                    "spot_name": spot.official_name if spot else None,
                }
            )
        return {"plan_id": plan_id, "stops": sorted(items, key=lambda x: x["position"])}


# --- 混雑集計：MV優先 -------------------------------------------------------

def _status_from_count(n: int) -> str:
    if n <= CONGESTION_THRESHOLDS["low_max"]:
        return "空いています"
    if n <= CONGESTION_THRESHOLDS["mid_max"]:
        return "比較的穏やかでしょう"
    return "混雑が予想されます"


def get_congestion_count(db: Session, *, spot_id: int, visit_date: date) -> int:
    """
    まずはマテビューを参照。無ければJOINでフォールバック。
    """
    # MV 参照
    try:
        cnt = db.execute(
            text(
                f"""
                SELECT user_count
                FROM {MV_NAME}
                WHERE spot_id = :spot_id AND visit_date = :visit_date
                """
            ),
            {"spot_id": spot_id, "visit_date": visit_date},
        ).scalar_one_or_none()
        if cnt is not None:
            return int(cnt)
    except Exception:
        # MVが未作成/未REFRESHの可能性 → JOIN集計へ
        pass

    # フォールバック：JOIN 集計
    cnt = db.execute(
        text(
            """
            SELECT COUNT(DISTINCT p.user_id) AS user_count
            FROM plans p
            JOIN stops s ON s.plan_id = p.id
            WHERE s.spot_id = :spot_id
              AND p.start_date = :visit_date
            """
        ),
        {"spot_id": spot_id, "visit_date": visit_date},
    ).scalar_one() or 0
    return int(cnt)


def get_congestion_info(spot_id: int, visit_date: date) -> Dict:
    """
    Information Service から呼ばれる想定の公開関数。
    """
    with _get_db() as db:
        cnt = get_congestion_count(db, spot_id=spot_id, visit_date=visit_date)
        return {
            "spot_id": spot_id,
            "date": str(visit_date),
            "count": cnt,
            "status": _status_from_count(cnt),
        }
