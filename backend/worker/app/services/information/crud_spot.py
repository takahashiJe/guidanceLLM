# worker/app/services/information/crud_spot.py

from typing import List, Optional
from sqlalchemy.orm import Session
from shared.app.models import Spot

def get_spot_by_id(db: Session, spot_id: str) -> Optional[Spot]:
    """
    指定されたspot_idを持つ単一のスポット情報をデータベースから取得する。

    Args:
        db (Session): SQLAlchemyのデータベースセッション。
        spot_id (str): 取得したいスポットのID。

    Returns:
        Optional[Spot]: 発見されたSpotオブジェクト。見つからない場合はNone。
    """
    return db.query(Spot).filter(Spot.spot_id == spot_id).first()


def search_spots_by_name(db: Session, name: str, language: str) -> List[Spot]:
    """
    指定された名称に部分一致するスポットのリストを検索する。
    検索はケースを区別しない（case-insensitive）。

    Args:
        db (Session): SQLAlchemyのデータベースセッション。
        name (str): 検索する名称の文字列。
        language (str): 検索対象の言語 ('ja', 'en', 'zh')。

    Returns:
        List[Spot]: 発見されたSpotオブジェクトのリスト。
    """
    query_filter = None
    search_pattern = f"%{name}%"

    if language == 'en':
        query_filter = Spot.official_name_en.ilike(search_pattern)
    elif language == 'zh':
        query_filter = Spot.official_name_zh.ilike(search_pattern)
    else: # デフォルトは日本語
        query_filter = Spot.official_name_ja.ilike(search_pattern)

    return db.query(Spot).filter(query_filter).all()


def get_spots_by_tag(db: Session, tag: str, language: str) -> List[Spot]:
    """
    指定されたタグを含むスポットのリストを検索する。

    Args:
        db (Session): SQLAlchemyのデータベースセッション。
        tag (str): 検索するタグ文字列。
        language (str): 検索対象の言語 ('ja', 'en', 'zh')。

    Returns:
        List[Spot]: 発見されたSpotオブジェクトのリスト。
    """
    query_filter = None
    
    if language == 'en':
        # tags_enカラム(ARRAY型)に指定したtagが含まれているかを判定
        query_filter = Spot.tags_en.any(tag)
    elif language == 'zh':
        query_filter = Spot.tags_zh.any(tag)
    else: # デフォルトは日本語
        query_filter = Spot.tags_ja.any(tag)

    return db.query(Spot).filter(query_filter).all()