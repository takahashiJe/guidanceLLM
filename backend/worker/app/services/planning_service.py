# /backend/worker/app/services/planning_service.py

import traceback
from datetime import date, timedelta
from typing import Dict, Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from app.db import models

# 混雑と判断する予約数の閾値
CONGESTION_THRESHOLD = 10

def get_or_create_user(db: Session, user_id: str) -> models.User:
    """
    指定されたuser_idのユーザーを取得、存在しない場合は新規作成する。
    """
    user = db.query(models.User).filter(models.User.user_id == user_id).first()
    if not user:
        print(f"User with user_id='{user_id}' not found. Creating new user.")
        user = models.User(user_id=user_id)
        db.add(user)
        db.flush()
    return user

def get_plan(db: Session, user_id: str) -> Optional[models.VisitPlan]:
    user = db.query(models.User).filter(models.User.user_id == user_id).first()
    if not user:
        return None
    return db.query(models.VisitPlan).filter(models.VisitPlan.user_id == user.user_id).first()

def check_congestion_on_date(db: Session, spot_id: str, visit_date: date) -> int:
    """
    指定されたspot_idと日付の計画数をカウントする。
    """
    return db.query(models.VisitPlan).filter(
        models.VisitPlan.spot_id == spot_id,
        func.date(models.VisitPlan.visit_date) == visit_date
    ).count()

def get_congestion_for_range(db: Session, spot_id: str, start_date: date, end_date: date) -> Dict[date, int]:
    """
    指定されたspot_idと期間の計画数を日毎に集計する。
    """
    results = db.query(
        func.date(models.VisitPlan.visit_date).label("visit_day"),
        func.count(models.VisitPlan.id).label("plan_count")
    ).filter(
        models.VisitPlan.spot_id == spot_id, 
        func.date(models.VisitPlan.visit_date).between(start_date, end_date)
    ).group_by("visit_day").all()
    return {result.visit_day: result.plan_count for result in results}

def process_plan_creation(db: Session, user_id: str, spot_id: str, spot_name: str, visit_date: date) -> Dict[str, Any]:
    try:
        user = get_or_create_user(db, user_id)
        existing_plan = get_plan(db, user_id)

        if existing_plan:
            existing_plan.spot_id = spot_id
            existing_plan.spot_name = spot_name
            existing_plan.visit_date = visit_date
        else:
            new_plan = models.VisitPlan(
                user_id=user.user_id, 
                spot_id=spot_id,
                spot_name=spot_name, 
                visit_date=visit_date
            )
            db.add(new_plan)

        congestion_level = check_congestion_on_date(db, spot_id, visit_date)
        
        return {
            "status": "saved",
            "spot_id": spot_id,
            "spot_name": spot_name,
            "visit_date": visit_date.isoformat(),
            "congestion": congestion_level,
            "is_congested": congestion_level >= CONGESTION_THRESHOLD
        }
    except Exception:
        traceback.print_exc()
        return {"status": "error", "message": "計画の保存準備中に予期せぬエラーが発生しました。"}

def process_plan_range_check(db: Session, spot_id: str, spot_name: str, start_date: date, end_date: date) -> Dict[str, Any]:
    """
    指定された期間の混雑状況をspot_idを基準にチェックする。
    """
    try:
        # spot_id を渡して混雑度マップを取得
        congestion_map = get_congestion_for_range(db, spot_id, start_date, end_date)
        formatted_map = {dt.isoformat(): count for dt, count in congestion_map.items()}
        return {
            "status": "checked",
            "spot_id": spot_id,
            "spot_name": spot_name,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "congestion_map": formatted_map
        }
    except Exception:
        traceback.print_exc()
        return {"status": "error", "message": "期間の混雑状況チェック中に予期せぬエラーが発生しました。"}

def process_plan_deletion(db: Session, user_id: str) -> Dict[str, Any]:
    try:
        plan_to_delete = get_plan(db, user_id)
        if plan_to_delete:
            db.delete(plan_to_delete)
            return {"status": "deleted", "message": "計画を削除しました。"}
        else:
            return {"status": "not_found", "message": "削除する計画が見つかりませんでした。"}
    except Exception:
        traceback.print_exc()
        return {"status": "error", "message": "計画の削除準備中に予期せぬエラーが発生しました。"}