# worker/app/services/itinerary/crud_plan.py

from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from shared.app.models import Plan, Stop

# ==============================================================================
# Planテーブルに対するCRUD操作
# ==============================================================================

def create_plan(db: Session, user_id: int, plan_name: Optional[str] = "新しい計画") -> Plan:
    """
    新しいPlanレコードをデータベースに作成する。

    Args:
        db (Session): SQLAlchemyのデータベースセッション。
        user_id (int): 計画を作成するユーザーのID。
        plan_name (Optional[str]): 計画の名称。

    Returns:
        Plan: 作成されたPlanオブジェクト。
    """
    new_plan = Plan(user_id=user_id, plan_name=plan_name)
    db.add(new_plan)
    db.commit()
    db.refresh(new_plan)
    return new_plan

def get_plan_by_id(db: Session, plan_id: int) -> Optional[Plan]:
    """
    指定されたplan_idを持つPlanレコードをデータベースから取得する。

    Args:
        db (Session): SQLAlchemyのデータベースセッション。
        plan_id (int): 取得したい計画のID。

    Returns:
        Optional[Plan]: 発見されたPlanオブジェクト。見つからない場合はNone。
    """
    return db.query(Plan).filter(Plan.plan_id == plan_id).first()

# ==============================================================================
# Stopテーブルに対するCRUD操作
# ==============================================================================

def get_stops_by_plan_id(db: Session, plan_id: int) -> List[Stop]:
    """
    特定の計画に紐づく全てのStopレコードを、stop_orderの昇順で取得する。

    Args:
        db (Session): SQLAlchemyのデータベースセッション。
        plan_id (int): 訪問先リストを取得したい計画のID。

    Returns:
        List[Stop]: 発見されたStopオブジェクトのリスト。
    """
    return db.query(Stop).filter(Stop.plan_id == plan_id).order_by(Stop.stop_order).all()

def get_highest_stop_order(db: Session, plan_id: int) -> int:
    """
    特定の計画内で現在最も大きいstop_orderの値を返す。Stopがなければ0を返す。

    Args:
        db (Session): SQLAlchemyのデータベースセッション。
        plan_id (int): 計画のID。

    Returns:
        int: 最も大きいstop_orderの値。
    """
    max_order = db.query(func.max(Stop.stop_order)).filter(Stop.plan_id == plan_id).scalar()
    return max_order or 0

def create_stop(db: Session, plan_id: int, spot_id: str, stop_order: int) -> Stop:
    """
    新しいStopレコードをデータベースに作成する。

    Args:
        db (Session): SQLAlchemyのデータベースセッション。
        plan_id (int): Stopを追加する計画のID。
        spot_id (str): 追加するスポットのID。
        stop_order (int): このStopの訪問順。

    Returns:
        Stop: 作成されたStopオブジェクト。
    """
    new_stop = Stop(plan_id=plan_id, spot_id=spot_id, stop_order=stop_order)
    db.add(new_stop)
    db.commit()
    db.refresh(new_stop)
    return new_stop

def delete_stop_by_id(db: Session, stop_id: int) -> bool:
    """
    指定されたstop_idを持つStopレコードをデータベースから削除する。

    Args:
        db (Session): SQLAlchemyのデータベースセッション。
        stop_id (int): 削除したいStopのID。

    Returns:
        bool: 削除が成功した場合はTrue、対象が見つからなかった場合はFalse。
    """
    stop_to_delete = db.query(Stop).filter(Stop.stop_id == stop_id).first()
    if stop_to_delete:
        db.delete(stop_to_delete)
        db.commit()
        return True
    return False

def bulk_update_stop_orders(db: Session, plan_id: int, stop_updates: List[dict]):
    """
    特定の計画内の複数のStopのstop_orderを一括で更新する。

    Args:
        db (Session): SQLAlchemyのデータベースセッション。
        plan_id (int): 更新対象の計画のID。
        stop_updates (List[dict]): 更新内容のリスト。各辞書は{'stop_id': int, 'stop_order': int}の形式。
    """
    # SQLAlchemyのbulk_update_mappingsを使うことで、効率的な一括更新が可能
    db.bulk_update_mappings(Stop, stop_updates)
    db.commit()