# -*- coding: utf-8 -*-
"""
情報提供サービス部向けの Spot 検索 CRUD。
要件:
- FR-3-1: 正式名称/カテゴリ/一般観光でスポット特定
- FR-3-5: 漠然質問では tourist_spot のみ
"""

from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from shared.app import models


def find_spots_by_official_name(
    db: Session, query: str, language: str, limit: int = 20
) -> List[models.Spot]:
    """部分一致で official_name_xx を検索（固有名詞）"""
    col = {
        "ja": models.Spot.official_name_ja,
        "en": models.Spot.official_name_en,
        "zh": models.Spot.official_name_zh,
    }.get(language, models.Spot.official_name_ja)

    return (
        db.query(models.Spot)
        .filter(col.ilike(f"%{query}%"))
        .order_by(models.Spot.popularity.desc().nullslast())
        .limit(limit)
        .all()
    )


def find_spots_by_tag(
    db: Session, tag: str, limit: int = 30
) -> List[models.Spot]:
    """tags(JSON/CSV いずれでも) に tag を含むレコードを検索"""
    # tags が TEXT(JSON) の想定：文字列包含で簡易実装（正規化済みDBであれば中間テーブルJOINに置換）
    like = f"%{tag}%"
    return (
        db.query(models.Spot)
        .filter(or_(models.Spot.tags.ilike(like), models.Spot.category.ilike(like)))
        .order_by(models.Spot.popularity.desc().nullslast())
        .limit(limit)
        .all()
    )


def list_general_tourist_spots(
    db: Session, limit: int = 50
) -> List[models.Spot]:
    """観光スポットのみ（宿泊は除外）"""
    return (
        db.query(models.Spot)
        .filter(models.Spot.spot_type == "tourist_spot")
        .order_by(models.Spot.popularity.desc().nullslast())
        .limit(limit)
        .all()
    )


def get_spot_by_id(db: Session, spot_id: int) -> Optional[models.Spot]:
    return db.query(models.Spot).filter(models.Spot.id == spot_id).first()
