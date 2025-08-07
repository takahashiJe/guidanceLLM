# worker/app/services/itinerary/itinerary_service.py

from typing import List, Optional, Dict
from sqlalchemy.orm import Session
from datetime import date


from shared.app.models import Plan, Stop
from worker.app.services.itinerary import crud_plan

class ItineraryService:
    """
    周遊計画の作成と編集に関するビジネスロジックを担うサービスクラス。
    このクラスが、対話オーケストレーション部からの唯一の窓口となる。
    """

    def create_new_plan(self, db: Session, user_id: int, plan_name: Optional[str] = "新しい計画") -> Plan:
        """
        [FR-4-1] 新しい周遊計画を作成する。

        Args:
            db (Session): データベースセッション。
            user_id (int): 計画を作成するユーザーのID。
            plan_name (Optional[str]): 計画の名称。

        Returns:
            Plan: 作成されたPlanオブジェクト。
        """
        return crud_plan.create_plan(db, user_id=user_id, plan_name=plan_name)

    def get_plan_details(self, db: Session, plan_id: int) -> Optional[Plan]:
        """
        計画の全詳細（訪問先リストを含む）を取得する。

        Args:
            db (Session): データベースセッション。
            plan_id (int): 取得したい計画のID。

        Returns:
            Optional[Plan]: 訪問先リスト（stopsリレーション）がロードされたPlanオブジェクト。
        """
        return crud_plan.get_plan_by_id(db, plan_id=plan_id)

    def add_spot_to_plan(self, db: Session, plan_id: int, spot_id: str) -> Stop:
        """
        [FR-4-2] 計画に新しい訪問先を末尾に追加する。

        Args:
            db (Session): データベースセッション。
            plan_id (int): 訪問先を追加する計画のID。
            spot_id (str): 追加するスポットのID。

        Returns:
            Stop: 新しく追加されたStopオブジェクト。
        """
        # 1. 現在の最大の訪問順を取得する
        highest_order = crud_plan.get_highest_stop_order(db, plan_id=plan_id)
        new_order = highest_order + 1

        # 2. 新しい訪問先をDBに作成する
        return crud_plan.create_stop(db, plan_id=plan_id, spot_id=spot_id, stop_order=new_order)

    def remove_spot_from_plan(self, db: Session, plan_id: int, stop_id: int) -> List[Stop]:
        """
        [FR-4-2] 計画から訪問先を削除し、残りの訪問先の順序を詰める。

        Args:
            db (Session): データベースセッション。
            plan_id (int): 訪問先を削除する計画のID。
            stop_id (int): 削除する訪問先のID（Stopテーブルの主キー）。

        Returns:
            List[Stop]: 更新後の訪問先リスト。
        """
        # 1. 指定された訪問先を削除する
        deleted = crud_plan.delete_stop_by_id(db, stop_id=stop_id)
        if not deleted:
            # 削除対象が見つからなかった場合は、現在のリストをそのまま返す
            return crud_plan.get_stops_by_plan_id(db, plan_id=plan_id)

        # 2. 削除後、残った訪問先リストを取得する
        remaining_stops = crud_plan.get_stops_by_plan_id(db, plan_id=plan_id)

        # 3. 順序が飛んでいる場合（例: 1, 3, 4）、連番に修正するための更新データを作成
        updates_to_perform = []
        for i, stop in enumerate(remaining_stops):
            new_order = i + 1
            if stop.stop_order != new_order:
                updates_to_perform.append({'stop_id': stop.stop_id, 'stop_order': new_order})

        # 4. 必要な更新があれば、一括で実行する
        if updates_to_perform:
            crud_plan.bulk_update_stop_orders(db, plan_id=plan_id, stop_updates=updates_to_perform)

        # 5. 最終的な訪問先リストを再取得して返す
        return crud_plan.get_stops_by_plan_id(db, plan_id=plan_id)

    def reorder_plan_stops(self, db: Session, plan_id: int, ordered_stop_ids: List[int]) -> List[Stop]:
        """
        [FR-4-2] 計画の訪問先の順序を、指定されたリスト通りに一括で更新する。

        Args:
            db (Session): データベースセッション。
            plan_id (int): 順序変更する計画のID。
            ordered_stop_ids (List[int]): 新しい順序でのstop_idのリスト。

        Returns:
            List[Stop]: 更新後の訪問先リスト。
        """
        # 1. 更新用データを作成する
        updates_to_perform = []
        for i, stop_id in enumerate(ordered_stop_ids):
            updates_to_perform.append({'stop_id': stop_id, 'stop_order': i + 1})

        # 2. 更新を実行する
        if updates_to_perform:
            crud_plan.bulk_update_stop_orders(db, plan_id=plan_id, stop_updates=updates_to_perform)
        
        # 3. 更新後のリストを返却する
        return crud_plan.get_stops_by_plan_id(db, plan_id=plan_id)
    
    def get_congestion_info(self, db: Session, spot_id: str, target_date: date) -> int:
        """
        指定された日・スポットの計画人数（混雑情報）を返す。
        これは他のサービスから呼び出されることを想定した公開インターフェース。
        """
        # 内部の道具（crud_plan）を使って処理を実行する
        return crud_plan.get_plan_count_for_spot_on_date(
            db, spot_id=spot_id, target_date=target_date
        )