# /backend/worker/app/services/planning_service.py
# ユーザーの訪問計画に関するロジック

from datetime import date, timedelta
from typing import Dict, Any

from sqlalchemy.orm import Session
from ..db.session import SessionLocal
from ..db.models import VisitPlan

# 混雑と判断する予約数の閾値
CONGESTION_THRESHOLD = 10

def process_visit_plan(user_id: str, spot_name: str, visit_date: date) -> Dict[str, Any]:
    """
    指定されたスポットと日付の混雑状況を確認し、計画を登録した上で、
    状況に応じたメッセージを返します。
    """
    db: Session = SessionLocal()
    try:
        # 1. 指定されたスポットと日付の既存の計画数をカウント
        existing_plans_count = (
            db.query(VisitPlan)
            .filter(
                VisitPlan.spot_name == spot_name,
                VisitPlan.visit_date == visit_date
            )
            .count()
        )

        # ★★★ 変更点: 混雑状況に関わらず、まず計画をDBに登録します ★★★
        new_plan = VisitPlan(
            user_id=user_id,
            spot_name=spot_name,
            visit_date=visit_date
        )
        db.add(new_plan)
        db.commit()

        # 2. 混雑状況に応じてユーザーへのメッセージを決定
        if existing_plans_count >= CONGESTION_THRESHOLD:
            # 混雑している場合のメッセージ
            message = (
                f"{spot_name}への{visit_date.strftime('%Y-%m-%d')}の訪問計画を登録しました。"
                f"ただし、その日は混雑が予想されますのでご注意ください。"
            )
        else:
            # 空いている場合のメッセージ
            message = f"{spot_name}への{visit_date.strftime('%Y-%m-%d')}の訪問計画を登録しました。"

        # 3. 登録は成功しているので、ステータスは"available"とし、適切なメッセージを返す
        return {
            "status": "available",
            "message": message
        }
            
    except Exception as e:
        db.rollback()
        print(f"Error in process_visit_plan: {e}")
        return {"status": "error", "message": "訪問計画の処理中にエラーが発生しました。"}
    finally:
        db.close()