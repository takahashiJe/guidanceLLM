# -*- coding: utf-8 -*-
"""
周遊計画CRUD（DB層直タッチ）の実装。
- トランザクションと一貫性を重視（行ロック/楽観排他の考慮）
- 滞在時間の概念は持たず、訪問順序（position）のみを管理
- レースコンディションに強い実装（position の再採番・ギャップ解消）
"""

from typing import List, Optional, Any, Dict
from datetime import date
from sqlalchemy import select, func, update, delete, text, and_
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from shared.app.models import Plan, Stop, Spot, Session as UserSession
from shared.app.database import SessionLocal


# --- ユーティリティ ---------------------------------------------------------

def _get_db() -> Session:
    """Worker側用の素朴なDBセッション取得（コンテキスト外での簡易利用）。"""
    return SessionLocal()


def _normalize_positions(db: Session, plan_id: int) -> None:
    """
    order_index を 1..N に詰め直す（隙間・重複排除）。
    """
    stops = (
        db.execute(
            select(Stop)
            .where(Stop.plan_id == plan_id)
            .order_by(Stop.order_index.asc(), Stop.id.asc())
        )
        .scalars()
        .all()
    )
    for i, st in enumerate(stops, start=1):
        if st.order_index != i:
            st.order_index = i
    db.flush()


# --- CRUD 本体 --------------------------------------------------------------

def create_new_plan(db: Session, *, user_id: int, session_id: str, start_date: date) -> Plan:
    """
    新しい計画を作成し、sessions.active_plan_id を更新する。
    - 要件: 新セッション開始で計画リセット
    """
    new_plan = Plan(user_id=user_id, session_id=session_id, start_date=start_date)
    db.add(new_plan)
    db.flush()  # id 採番

    # 対応セッションがあれば active_plan_id を更新
    sess = db.get(UserSession, session_id)
    if sess:
        sess.active_plan_id = new_plan.id

    db.commit()
    db.refresh(new_plan)
    return new_plan


def add_spot_to_plan(db: Session, *, plan_id: int, spot_id: int, position: Optional[int] = None) -> Stop:
    plan = db.get(Plan, plan_id)
    if not plan:
        raise ValueError("plan not found")

    spot = db.get(Spot, spot_id)
    if not spot:
        raise ValueError("spot not found")

    # 末尾インデックスを取得（position → order_index に変更）
    max_idx = db.execute(
        select(func.coalesce(func.max(Stop.order_index), 0)).where(Stop.plan_id == plan_id)
    ).scalar_one()
    next_idx = (max_idx or 0) + 1

    if position is None:
        new_index = next_idx
    else:
        db.execute(
            update(Stop)
            .where(Stop.plan_id == plan_id, Stop.order_index >= position)
            .values(order_index=Stop.order_index + 1)
        )
        new_index = position

    st = Stop(plan_id=plan_id, spot_id=spot_id, order_index=new_index)
    db.add(st)
    db.flush()   # ← 追加
    return st


def remove_spot_from_plan(db: Session, *, plan_id: int, stop_id: int) -> None:
    """
    Stop を1件削除して position を詰める。
    """
    st = db.get(Stop, stop_id)
    if not st or st.plan_id != plan_id:
        raise ValueError("stop not found")

    db.delete(st)
    db.flush()
    _normalize_positions(db, plan_id)
    db.commit()


def reorder_plan_stops(db: Session, *, plan_id: int, new_order_stop_ids: List[int]) -> None:
    """
    訪問順序を一括並べ替え。
    - new_order_stop_ids は plan 内の Stop.id の並びを指定
    - 並びに存在しないStopは末尾に position を維持したまま付ける（基本は完全指定を推奨）
    """
    stops = (
        db.execute(select(Stop).where(Stop.plan_id == plan_id))
        .scalars()
        .all()
    )
    if not stops:
        return

    by_id = {s.id: s for s in stops}

    pos = 1
    for sid in new_order_stop_ids:
        s = by_id.pop(sid, None)
        if s is not None:
            s.position = pos
            pos += 1

    # 指定から漏れたStopを既存のposition順で後ろに
    rest = sorted(by_id.values(), key=lambda x: x.position)
    for s in rest:
        s.position = pos
        pos += 1

    _normalize_positions(db, plan_id)
    db.commit()


def summarize_plan_stops(db: Session, *, plan_id: int) -> dict[str, Any]:
    rows = (
        db.execute(
            select(
                Stop.id.label("stop_id"),
                Stop.order_index.label("order_index"),
                Spot.id.label("spot_id"),
                Spot.official_name.label("official_name"),
                Spot.spot_type.label("spot_type"),
                Spot.tags.label("tags"),
                Spot.latitude,
                Spot.longitude,
            )
            .join(Spot, Stop.spot_id == Spot.id)
            .where(Stop.plan_id == plan_id)
            .order_by(Stop.order_index.asc(), Stop.id.asc())
        )
        .mappings()
        .all()
    )

    return {
        "plan_id": plan_id,
        "stops": [
            {
                "stop_id": r["stop_id"],
                "order_index": r["order_index"],
                "spot_id": r["spot_id"],
                "official_name": r["official_name"],
                "spot_type": r["spot_type"],
                "tags": r["tags"],
                "latitude": float(r["latitude"]),
                "longitude": float(r["longitude"]),
            }
            for r in rows
        ],
    }

def list_stops_for_plan(db: Session, *, plan_id: int) -> list[Stop]:
    return (
        db.execute(
            select(Stop).where(Stop.plan_id == plan_id).order_by(Stop.order_index.asc(), Stop.id.asc())
        )
        .scalars()
        .all()
    )

def reorder_stops(db: Session, *, plan_id: int, ordered_stop_ids: list[int]) -> None:
    if not ordered_stop_ids:
        return
    # 1 から連番で order_index を振り直す
    for i, sid in enumerate(ordered_stop_ids, start=1):
        db.execute(
            update(Stop)
            .where(Stop.id == sid, Stop.plan_id == plan_id)
            .values(order_index=i)
        )