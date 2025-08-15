# -*- coding: utf-8 -*-
"""
Itinerary（周遊計画）に関するDB CRUDを担当するモジュール。
- 要件: 滞在時間の概念なし、訪問順序のリストのみを管理（FR-4）
- ここでは spot_type によるフィルタは行わない（宿泊も観光も可）
- レースコンディション対策として、stop.order_index の整合性をトランザクション内で維持

提供関数:
- create_new_plan
- add_spot_to_plan
- remove_spot_from_plan
- reorder_plan_stops
- get_plan_count_for_spot_on_date（JOIN集計 or マテビュー利用のフォールバック付き）
"""

from datetime import date
from typing import List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import select, func, text

from shared.app.models import Plan, Stop, Spot, Session as UserSession  # セッションは名称衝突回避のためUserSessionで参照


def create_new_plan(db: Session, *, user_id: int, session_id: str, start_date: Optional[date], language: str) -> Plan:
    """
    新しい計画を作成し、sessions.active_plan_id を更新する。
    - start_date は混雑集計のキーに使われるため、可能なら指定する
    """
    # プラン作成
    plan = Plan(user_id=user_id, session_id=session_id, start_date=start_date, language=language)
    db.add(plan)
    db.flush()  # plan.id を取得

    # セッションの active_plan_id を更新
    sess = db.execute(
        select(UserSession).where(UserSession.session_id == session_id)
    ).scalar_one_or_none()
    if sess:
        sess.active_plan_id = plan.id
        sess.current_status = "planning"

    db.commit()
    db.refresh(plan)
    return plan


def _get_next_order_index(db: Session, plan_id: int) -> int:
    """当該プランの末尾 order_index を取得し、その+1を返す。"""
    max_idx = db.execute(
        select(func.coalesce(func.max(Stop.order_index), -1)).where(Stop.plan_id == plan_id)
    ).scalar_one()
    return int(max_idx) + 1


def add_spot_to_plan(db: Session, *, plan_id: int, spot_id: int, position: Optional[int] = None) -> Stop:
    """
    計画にスポットを追加。
    - position が None の場合は末尾に追加
    - position を指定した場合、挿入位置以降の order_index を +1 してから挿入
    """
    if position is None:
        order_index = _get_next_order_index(db, plan_id)
    else:
        # position 以降をシフト
        db.query(Stop).filter(Stop.plan_id == plan_id, Stop.order_index >= position).update(
            {Stop.order_index: Stop.order_index + 1}, synchronize_session=False
        )
        order_index = position

    stop = Stop(plan_id=plan_id, spot_id=spot_id, order_index=order_index)
    db.add(stop)
    db.commit()
    db.refresh(stop)
    return stop


def remove_spot_from_plan(db: Session, *, plan_id: int, stop_id: int) -> None:
    """
    指定 stop_id を削除し、後続の order_index を -1 で詰める。
    """
    stop = db.execute(
        select(Stop).where(Stop.id == stop_id, Stop.plan_id == plan_id)
    ).scalar_one_or_none()
    if not stop:
        return

    removed_index = stop.order_index
    db.delete(stop)
    # 後続の order_index を詰める
    db.query(Stop).filter(Stop.plan_id == plan_id, Stop.order_index > removed_index).update(
        {Stop.order_index: Stop.order_index - 1}, synchronize_session=False
    )
    db.commit()


def reorder_plan_stops(db: Session, *, plan_id: int, new_order_stop_ids: List[int]) -> None:
    """
    訪問順序の入れ替え。
    - new_order_stop_ids は新しい順序で並んだ stop.id の配列
    - 長さの整合性と当該 plan_id 所属チェックを行う
    """
    # 現状の stops を取得
    stops = db.execute(select(Stop).where(Stop.plan_id == plan_id).order_by(Stop.order_index)).scalars().all()
    if len(stops) != len(new_order_stop_ids):
        raise ValueError("new_order_stop_ids の長さが現在の Stops 数と一致しません。")

    # 所属チェック
    stop_ids_set = {s.id for s in stops}
    if set(new_order_stop_ids) != stop_ids_set:
        raise ValueError("new_order_stop_ids に未知の stop.id が含まれています。")

    # id -> Stop を辞書化して一括更新
    id_to_stop = {s.id: s for s in stops}
    for idx, sid in enumerate(new_order_stop_ids):
        id_to_stop[sid].order_index = idx

    db.commit()


def get_plan_count_for_spot_on_date(db: Session, *, target_date: date, spot_id: int) -> int:
    """
    指定日付・スポットの混雑件数を返す。
    - 可能であればマテリアライズドビュー(spot_congestion_mv)を優先
    - ない場合はJOIN集計にフォールバック
    """
    # まずマテビュー存在チェック→読み取り
    try:
        cnt = db.execute(
            text("""
                SELECT plan_count
                FROM spot_congestion_mv
                WHERE visit_date = :d AND spot_id = :sid
                LIMIT 1
            """),
            {"d": target_date, "sid": spot_id}
        ).scalar_one_or_none()
        if cnt is not None:
            return int(cnt)
    except Exception:
        # ビュー未作成や権限エラー時はJOINにフォールバック
        pass

    # JOIN集計
    cnt2 = db.execute(
        text("""
            SELECT COUNT(DISTINCT p.id)
            FROM plans p
            JOIN stops s ON s.plan_id = p.id
            WHERE p.start_date = :d AND s.spot_id = :sid
        """),
        {"d": target_date, "sid": spot_id}
    ).scalar_one()
    return int(cnt2)
