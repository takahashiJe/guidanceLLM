# -*- coding: utf-8 -*-
"""
ItineraryService: 周遊計画機能の公開インターフェース。
- CRUD と 混雑数/ステータス（しきい値バンド）を提供
- Information Service や Orchestrator から同期呼び出しされることを想定

混雑ステータスのしきい値は定数化し、要件の例と一致させる：
0〜10   -> 空いています
11〜30  -> 比較的穏やかでしょう
31+     -> 混雑が予想されます
"""

from datetime import date
from typing import List, Optional, Dict

from sqlalchemy.orm import Session

from shared.app.database import SessionLocal
from shared.app.models import Plan, Stop
from shared.app.schemas import (
    PlanCreateRequest, PlanResponse,
    StopCreateRequest, StopsReorderRequest,
    CongestionStatusResponse
)

from .crud_plan import (
    create_new_plan, add_spot_to_plan, remove_spot_from_plan, reorder_plan_stops,
    get_plan_count_for_spot_on_date
)

# しきい値（必要なら .env or 設定に逃がしても良い）
CONGESTION_BANDS = [
    (0, 10, "空いています"),
    (11, 30, "比較的穏やかでしょう"),
    (31, 10**9, "混雑が予想されます"),
]


def _judge_congestion_status(count: int) -> str:
    for low, high, label in CONGESTION_BANDS:
        if low <= count <= high:
            return label
    return "不明"


class ItineraryService:
    """周遊計画のユースケースを提供するサービスクラス。"""

    # === CRUD ===
    def create_new_plan(self, req: PlanCreateRequest) -> PlanResponse:
        with SessionLocal() as db:
            plan = create_new_plan(
                db,
                user_id=req.user_id,
                session_id=req.session_id,
                start_date=req.start_date,
                language=req.language,
            )
            return PlanResponse(id=plan.id, user_id=plan.user_id, session_id=plan.session_id,
                                start_date=plan.start_date, language=plan.language)

    def add_spot_to_plan(self, req: StopCreateRequest) -> Dict:
        with SessionLocal() as db:
            stop = add_spot_to_plan(
                db, plan_id=req.plan_id, spot_id=req.spot_id, position=req.position
            )
            return {"stop_id": stop.id, "plan_id": stop.plan_id, "spot_id": stop.spot_id, "order_index": stop.order_index}

    def remove_spot_from_plan(self, plan_id: int, stop_id: int) -> None:
        with SessionLocal() as db:
            remove_spot_from_plan(db, plan_id=plan_id, stop_id=stop_id)

    def reorder_plan_stops(self, req: StopsReorderRequest) -> None:
        with SessionLocal() as db:
            reorder_plan_stops(db, plan_id=req.plan_id, new_order_stop_ids=req.stop_ids)

    # === 混雑 ===
    def get_congestion_info(self, target_date: date, spot_id: int) -> CongestionStatusResponse:
        """
        指定日付×スポットの混雑件数とステータス文字列を返す。
        Information Service のナッジ材料収集で利用される。
        """
        with SessionLocal() as db:
            count = get_plan_count_for_spot_on_date(db, target_date=target_date, spot_id=spot_id)
        status = _judge_congestion_status(count)
        return CongestionStatusResponse(spot_id=spot_id, date=target_date, count=count, status=status)
