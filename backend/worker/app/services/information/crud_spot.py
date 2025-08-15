# backend/worker/app/services/information/crud_spot.py
# スポット検索（FR-3-1, FR-3-5）を担う CRUD 層
from __future__ import annotations
from typing import List, Optional

from sqlalchemy.orm import Session as OrmSession
from sqlalchemy import select, or_, and_

from shared.app.models import Spot, SpotType

def _normalize_like(query: str) -> str:
    return f"%{query.strip()}%"

def find_spots_by_official_name(db: OrmSession, query: str, limit: int = 20) -> List[Spot]:
    q = (
        select(Spot)
        .where(Spot.official_name.ilike(_normalize_like(query)))
        .limit(limit)
    )
    return db.scalars(q).all()

def find_spots_by_tag(db: OrmSession, tag: str, limit: int = 50) -> List[Spot]:
    # tags をカンマ区切り文字列として LIKE マッチ
    like = _normalize_like(tag)
    q = select(Spot).where(Spot.tags.ilike(like)).limit(limit)
    return db.scalars(q).all()

def find_general_tourist_spots(db: OrmSession, limit: int = 50) -> List[Spot]:
    q = select(Spot).where(Spot.spot_type == SpotType.tourist_spot).limit(limit)
    return db.scalars(q).all()
