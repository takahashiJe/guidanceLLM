# worker/app/services/information/information_service.py

from typing import List, Optional
from sqlalchemy.orm import Session

# 作成したCRUD関数をインポート
from worker.app.services.information import crud_spot
from shared.app.models import Spot

class InformationService:
    """
    スポット情報の取得と提供に関するビジネスロジックを担うサービスクラス。
    """

    def find_spot_by_id(self, db: Session, spot_id: str) -> Optional[Spot]:
        """
        IDに基づいて単一のスポットを検索する。

        Args:
            db (Session): データベースセッション。
            spot_id (str): スポットID。

        Returns:
            Optional[Spot]: SpotオブジェクトまたはNone。
        """
        return crud_spot.get_spot_by_id(db=db, spot_id=spot_id)

    def find_spots_by_name(self, db: Session, name: str, language: str) -> List[Spot]:
        """
        名称に基づいて複数のスポットを検索する。

        Args:
            db (Session): データベースセッション。
            name (str): 検索キーワード。
            language (str): 検索言語。

        Returns:
            List[Spot]: Spotオブジェクトのリスト。
        """
        return crud_spot.search_spots_by_name(db=db, name=name, language=language)

    def find_spots_by_tag(self, db: Session, tag: str, language: str) -> List[Spot]:
        """
        タグに基づいて複数のスポットを検索する。

        Args:
            db (Session): データベースセッション。
            tag (str): 検索タグ。
            language (str): 検索言語。

        Returns:
            List[Spot]: Spotオブジェクトのリスト。
        """
        return crud_spot.get_spots_by_tag(db=db, tag=tag, language=language)