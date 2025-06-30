# /backend/worker/app/services/planning_service.py
# ユーザーの訪問計画に関するデータベース操作とビジネスロジック

from datetime import date, timedelta
from typing import Dict, Any, Optional, List

from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from app.db import session, models

# 混雑と判断する予約数の閾値
CONGESTION_THRESHOLD = 10

# --- ユーザー管理ヘルパー関数 ---
def get_or_create_user(db: Session, user_id: str) -> models.User:
    """
    指定されたuser_idのユーザーを取得、存在しない場合は新規作成する。
    """
    user = db.query(models.User).filter(models.User.user_id == user_id).first()
    if not user:
        print(f"User with user_id='{user_id}' not found. Creating new user.")
        user = models.User(user_id=user_id)
        db.add(user)
        # この時点ではコミットせず、後続の処理と同一トランザクションで行う
    return user

# --- データベース操作関数 (CRUD + 混雑チェック) ---

def get_plan(db: Session, user_id: str) -> Optional[models.VisitPlan]:
    """指定されたユーザーの現在の訪問計画をデータベースから取得する。"""
    return db.query(models.VisitPlan).filter(models.VisitPlan.user_id == user_id).first()

def create_or_update_plan(db: Session, user_id: str, spot_name: str, visit_date: date) -> models.VisitPlan:
    """
    ユーザーの訪問計画を新規作成または更新する。
    ユーザーが存在しない場合は自動的に作成する。
    """
    # --- ★★★ 修正点：計画保存の前にユーザーを確保する ★★★ ---
    user = get_or_create_user(db, user_id)
    
    # 既存の計画を探す
    existing_plan = get_plan(db, user_id)
    
    if existing_plan:
        existing_plan.spot_name = spot_name
        existing_plan.visit_date = visit_date
        plan = existing_plan
    else:
        plan = models.VisitPlan(
            user_id=user.user_id, # 確保したユーザーのIDを使用
            spot_name=spot_name,
            visit_date=visit_date
        )
        db.add(plan)
    
    # ユーザー作成と計画作成/更新をまとめてコミット
    db.commit()
    db.refresh(plan)
    return plan

def delete_plan(db: Session, user_id: str) -> bool:
    """指定されたユーザーの訪問計画をデータベースから削除する。"""
    plan_to_delete = get_plan(db, user_id)
    if plan_to_delete:
        db.delete(plan_to_delete)
        db.commit()
        return True
    return False

def check_congestion_on_date(db: Session, spot_name: str, visit_date: date) -> int:
    """指定された場所と日付の計画数をカウントして返す。"""
    count = (
        db.query(models.VisitPlan)
        .filter(
            models.VisitPlan.spot_name == spot_name,
            func.date(models.VisitPlan.visit_date) == visit_date
        )
        .count()
    )
    return count

def get_congestion_for_range(db: Session, spot_name: str, start_date: date, end_date: date) -> Dict[date, int]:
    """指定された期間内の各日付における、特定のスポットの計画数を取得する。"""
    results = (
        db.query(
            func.date(models.VisitPlan.visit_date).label("visit_day"),
            func.count(models.VisitPlan.id).label("plan_count")
        )
        .filter(
            models.VisitPlan.spot_name == spot_name,
            and_(
                func.date(models.VisitPlan.visit_date) >= start_date,
                func.date(models.VisitPlan.visit_date) <= end_date
            )
        )
        .group_by("visit_day")
        .all()
    )
    congestion_map = {result.visit_day: result.plan_count for result in results}
    return congestion_map

def delete_plan(db: Session, user_id: str) -> bool:
    """指定されたユーザーの訪問計画をデータベースから削除する。"""
    plan_to_delete = get_plan(db, user_id)
    if plan_to_delete:
        db.delete(plan_to_delete)
        db.commit()
        return True
    return False

def check_congestion_on_date(db: Session, spot_name: str, visit_date: date) -> int:
    """指定された場所と日付の計画数をカウントして返す。"""
    count = (
        db.query(models.VisitPlan)
        .filter(
            models.VisitPlan.spot_name == spot_name,
            # 日付のみで比較するために、DateTime型をDate型にキャストする
            func.date(models.VisitPlan.visit_date) == visit_date
        )
        .count()
    )
    return count

def get_congestion_for_range(db: Session, spot_name: str, start_date: date, end_date: date) -> Dict[date, int]:
    """指定された期間内の各日付における、特定のスポットの計画数を取得する。"""
    # 指定期間内の計画を取得
    results = (
        db.query(
            func.date(models.VisitPlan.visit_date).label("visit_day"),
            func.count(models.VisitPlan.id).label("plan_count")
        )
        .filter(
            models.VisitPlan.spot_name == spot_name,
            and_(
                func.date(models.VisitPlan.visit_date) >= start_date,
                func.date(models.VisitPlan.visit_date) <= end_date
            )
        )
        .group_by("visit_day")
        .all()
    )
    
    # {日付: 人数} の形式の辞書を作成して返す
    congestion_map = {result.visit_day: result.plan_count for result in results}
    return congestion_map

# --- 公開サービス関数 (ツールから呼び出される) ---

def process_plan_creation(user_id: str, spot_name: str, visit_date: date) -> Dict[str, Any]:
    """
    計画を保存し、その日の混雑状況をチェックして結果を返す。
    """
    db: Session = session.SessionLocal()
    try:
        # 1. 計画をDBに保存（または更新）
        create_or_update_plan(db, user_id, spot_name, visit_date)
        
        # 2. 保存した計画日の混雑状況をチェック
        congestion_level = check_congestion_on_date(db, spot_name, visit_date)
        
        # 3. AIが判断しやすいように構造化された辞書を返す
        response = {
            "status": "saved",
            "spot_name": spot_name,
            "visit_date": visit_date.isoformat(),
            "congestion": congestion_level,
            "is_congested": congestion_level >= CONGESTION_THRESHOLD
        }
        return response
    except Exception as e:
        db.rollback()
        print(f"Error in process_plan_creation: {e}")
        return {"status": "error", "message": "計画の保存中にエラーが発生しました。"}
    finally:
        db.close()

def process_plan_range_check(spot_name: str, start_date: date, end_date: date) -> Dict[str, Any]:
    """
    指定された期間の混雑状況をチェックして結果を返す。
    """
    db: Session = session.SessionLocal()
    try:
        congestion_map = get_congestion_for_range(db, spot_name, start_date, end_date)
        
        # 日付オブジェクトを文字列に変換
        formatted_map = {dt.isoformat(): count for dt, count in congestion_map.items()}

        return {
            "status": "checked",
            "spot_name": spot_name,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "congestion_map": formatted_map
        }
    except Exception as e:
        db.rollback()
        print(f"Error in process_plan_range_check: {e}")
        return {"status": "error", "message": "期間の混雑状況チェック中にエラーが発生しました。"}
    finally:
        db.close()

def process_plan_deletion(user_id: str) -> Dict[str, Any]:
    """
    計画を削除する。
    """
    db: Session = session.SessionLocal()
    try:
        deleted = delete_plan(db, user_id)
        if deleted:
            return {"status": "deleted", "message": "計画を削除しました。"}
        else:
            return {"status": "not_found", "message": "削除する計画が見つかりませんでした。"}
    except Exception as e:
        db.rollback()
        print(f"Error in process_plan_deletion: {e}")
        return {"status": "error", "message": "計画の削除中にエラーが発生しました。"}
    finally:
        db.close()
