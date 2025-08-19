# -*- coding: utf-8 -*-
"""
周遊計画CRUD（DB層直タッチ）の実装。
- トランザクションと一貫性を重視（行ロック/楽観排他の考慮）
- 滞在時間の概念は持たず、訪問順序（position）のみを管理
- レースコンディションに強い実装（position の再採番・ギャップ解消）
"""

from typing import List, Optional
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
    position を 1..N に詰め直す（隙間・重複排除）。
    """
    stops = (
        db.execute(
            select(Stop).where(Stop.plan_id == plan_id).order_by(Stop.position.asc(), Stop.id.asc())
        )
        .scalars()
        .all()
    )
    for i, st in enumerate(stops, start=1):
        if st.position != i:
            st.position = i
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
    sess = (
        db.execute(
            select(UserSession).where(UserSession.session_id == session_id).with_for_update()
        )
        .scalars()
        .first()
    )
    if sess:
        sess.active_plan_id = new_plan.id
        sess.current_status = "planning"

    db.commit()
    db.refresh(new_plan)
    return new_plan


def add_spot_to_plan(
    db: Session,
    *,
    plan_id: int,
    spot_id: int,
    position: Optional[int] = None,
) -> Stop:
    """
    スポットを計画に追加する。
    - spot_type による制限は行わない（観光/宿泊ともOK）
    - position 未指定なら末尾に追加
    - position 指定時は以降をシフト
    """
    plan = db.get(Plan, plan_id)
    if not plan:
        raise ValueError("plan not found")

    # Spotの存在チェック
    spot = db.get(Spot, spot_id)
    if not spot:
        raise ValueError("spot not found")

    # 現在の末尾計算
    max_pos = db.execute(
        select(func.coalesce(func.max(Stop.position), 0)).where(Stop.plan_id == plan_id)
    ).scalar_one()

    insert_pos = max_pos + 1 if position is None else max(1, min(position, max_pos + 1))

    # position >= insert_pos を +1 シフト
    if insert_pos <= max_pos:
        db.execute(
            update(Stop)
            .where(and_(Stop.plan_id == plan_id, Stop.position >= insert_pos))
            .values(position=Stop.position + 1)
        )

    st = Stop(plan_id=plan_id, spot_id=spot_id, position=insert_pos)
    db.add(st)
    db.flush()

    _normalize_positions(db, plan_id)
    db.commit()
    db.refresh(st)
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


def summarize_plan_stops(db: Session, *, plan_id: int) -> List[Stop]:
    """
    現在の並び順でStopを返す（要約用の材料）。
    """
    stops = (
        db.execute(
            select(Stop).where(Stop.plan_id == plan_id).order_by(Stop.position.asc(), Stop.id.asc())
        )
        .scalars()
        .all()
    )
    return stops
