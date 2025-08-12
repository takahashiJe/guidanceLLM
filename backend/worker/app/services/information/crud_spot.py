# worker/app/services/information/crud_spot.py

from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func, cast, Date
from datetime import date
import logging

from shared.app.models import Spot, Plan, Stop

logger = logging.getLogger(__name__)

def get_spot_by_id(db: Session, spot_id: str) -> Optional[Spot]:
    """[責務3] IDで単一のスポット情報を取得する。"""
    try:
        return db.query(Spot).filter(Spot.spot_id == spot_id).first()
    except SQLAlchemyError as e:
        logger.error(f"DB error in get_spot_by_id for spot_id {spot_id}: {e}", exc_info=True)
        raise # エラーを呼び出し元に伝播させる

def find_spots_by_name(db: Session, name: str, language: str) -> List[Spot]:
    """[責務1] 固有名詞でスポットを検索する。"""
    try:
        query_filter = None
        search_pattern = f"%{name}%"
        
        if language == 'en':
            query_filter = Spot.official_name_en.ilike(search_pattern)
        elif language == 'zh':
            query_filter = Spot.official_name_zh.ilike(search_pattern)
        else:
            query_filter = Spot.official_name_ja.ilike(search_pattern)
            
        return db.query(Spot).filter(query_filter).all()
    except SQLAlchemyError as e:
        logger.error(f"DB error in find_spots_by_name for name {name}: {e}", exc_info=True)
        raise

def find_spots_by_tag(db: Session, tag: str, language: str) -> List[Spot]:
    """[責務1] 「絶景」などのタグでスポットを検索する。"""
    try:
        query_filter = None
        
        if language == 'en':
            query_filter = Spot.tags_en.any(tag)
        elif language == 'zh':
            query_filter = Spot.tags_zh.any(tag)
        else:
            query_filter = Spot.tags_ja.any(tag)
            
        return db.query(Spot).filter(query_filter).all()
    except SQLAlchemyError as e:
        logger.error(f"DB error in find_spots_by_tag for tag {tag}: {e}", exc_info=True)
        raise

def find_spots_by_type(db: Session, spot_type: str) -> List[Spot]:
    """[責務1] 「tourist_spot」などの種別でスポットを検索する。"""
    try:
        return db.query(Spot).filter(Spot.spot_type == spot_type).all()
    except SQLAlchemyError as e:
        logger.error(f"DB error in find_spots_by_type for type {spot_type}: {e}", exc_info=True)
        raise

def get_plan_count_for_spot_on_date(db: Session, spot_id: str, target_date: date) -> int:
    """
    指定された日に、特定のスポットを訪問計画に含んでいるユーザーの総数を取得する。
    """
    try:
        count = (
            db.query(func.count(Plan.plan_id))
            .join(Stop, Plan.plan_id == Stop.plan_id)
            .filter(Stop.spot_id == spot_id)
            .filter(cast(Plan.start_date, Date) == target_date)
            .scalar()
        )
        return count or 0
    except SQLAlchemyError as e:
        logger.error(f"DB error in get_plan_count_for_spot_on_date for spot {spot_id}: {e}", exc_info=True)
        # DBエラー時は0を返し、混雑予測が「空いている」と判断されるようにする
        return 0
