# -*- coding: utf-8 -*-
"""
Itinerary Service（本フェーズでは混雑集計に必要な最小限の公開APIのみ）。
- get_plan_count_for_spot_on_date: plans.start_date と stops.spot_id の JOIN 集計
"""

from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from shared.app import models


class ItineraryService:
    def get_plan_count_for_spot_on_date(
        self, db: Session, *, spot_id: int, date_str: str
    ) -> int:
        """
        指定スポットを含む「その日付の」計画のユーザー数を返す。
        JOIN: plans(start_date) x stops(spot_id)
        """
        q = (
            db.query(func.count(models.Plan.id).label("cnt"))
            .join(models.Stop, models.Plan.id == models.Stop.plan_id)
            .filter(models.Plan.start_date == date_str)
            .filter(models.Stop.spot_id == spot_id)
        )
        return int(q.scalar() or 0)
