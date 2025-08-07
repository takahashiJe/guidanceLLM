# worker/app/services/information/crud_spot.py

from typing import List, Optional
from sqlalchemy.orm import Session
from shared.app.models import Spot

def get_spot_by_id(db: Session, spot_id: str) -> Optional[Spot]:
    """[責務3] IDで単一のスポット情報を取得する。"""
    return db.query(Spot).filter(Spot.spot_id == spot_id).first()

def find_spots_by_name(db: Session, name: str, language: str) -> List[Spot]:
    """[責務1] 固有名詞でスポットを検索する。"""
    query_filter = None
    search_pattern = f"%{name}%"
    
    # 多言語対応
    if language == 'en':
        query_filter = Spot.official_name_en.ilike(search_pattern)
    elif language == 'zh':
        query_filter = Spot.official_name_zh.ilike(search_pattern)
    else:
        query_filter = Spot.official_name_ja.ilike(search_pattern)
        
    return db.query(Spot).filter(query_filter).all()

def find_spots_by_tag(db: Session, tag: str, language: str) -> List[Spot]:
    """[責務1] 「絶景」などのタグでスポットを検索する。"""
    query_filter = None
    
    if language == 'en':
        query_filter = Spot.tags_en.any(tag)
    elif language == 'zh':
        query_filter = Spot.tags_zh.any(tag)
    else:
        query_filter = Spot.tags_ja.any(tag)
        
    return db.query(Spot).filter(query_filter).all()

def find_spots_by_type(db: Session, spot_type: str) -> List[Spot]:
    """[責務1] 「tourist_spot」などの種別でスポットを検索する。"""
    return db.query(Spot).filter(Spot.spot_type == spot_type).all()

def get_plan_count_for_spot_on_date(db: Session, spot_id: str, target_date: date) -> int:
    """
    指定された日に、特定のスポットを訪問計画に含んでいるユーザーの総数を取得する。
    Plan.start_date (DateTime) と target_date (Date) を比較するため、日付部分のみを抽出して比較する。

    Args:
        db (Session): SQLAlchemyのデータベースセッション。
        spot_id (str): 調査対象のスポットID。
        target_date (date): 調査対象の日付。

    Returns:
        int: 該当する計画の総数。
    """
    count = (
        db.query(func.count(Plan.plan_id))
        .join(Stop, Plan.plan_id == Stop.plan_id)
        .filter(Stop.spot_id == spot_id)
        .filter(cast(Plan.start_date, Date) == target_date)
        .scalar()
    )
    return count or 0