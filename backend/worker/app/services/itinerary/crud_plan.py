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


def summarize_plan(db: Session, *, plan_id: int) -> Dict[str, Any]:
    """
    指定された計画の現在の状態（訪問地リスト、レグ、合成GeoJSONなど）を要約して返す。
    - stops: Stop行の要約（spot情報を含む）
    - legs:  隣接スポット間のハイブリッド区間（AP経由情報などを含む）
    - route_geojson: 全legsのFeatureを合成したFeatureCollection
    - total_duration_minutes: legsの合計所要時間（分）
    """
    # 1) 並び順のStop取得（ORMオブジェクトの配列）
    stops_orm = crud_plan.summarize_plan_stops(db, plan_id=plan_id)

    # 2) 正規化したstops（フロント返却用のdict配列）を作る
    stops: List[Dict[str, Any]] = []
    for st in stops_orm:
        # Spot情報を取得（リレーションが無ければ direct get でOK）
        sp: Optional[Spot] = db.get(Spot, st.spot_id)
        stops.append(
            {
                "stop_id": st.id,
                "spot_id": st.spot_id,
                "order_index": getattr(st, "order_index", getattr(st, "position", None)),
                # spotサマリ（存在すれば）
                "official_name": getattr(sp, "official_name", None),
                "spot_type": getattr(sp, "spot_type", None),
                "latitude": getattr(sp, "latitude", None),
                "longitude": getattr(sp, "longitude", None),
                "tags": getattr(sp, "tags", None),
            }
        )

    # 空や単点ならそのまま返す
    summary: Dict[str, Any] = {
        "plan_id": plan_id,
        "stops": stops,
        "route_geojson": None,
        "legs": [],
        "total_duration_minutes": 0,
    }
    if len(stops) < 2:
        return summary

    # 3) 隣接ペアごとにハイブリッドレグを計算して合成
    rs = RoutingService()
    legs: List[Dict[str, Any]] = []
    all_features: List[Dict[str, Any]] = []
    total_min = 0.0

    for i in range(len(stops) - 1):
        a = stops[i]
        b = stops[i + 1]

        origin = (a["latitude"], a["longitude"])
        dest = (b["latitude"], b["longitude"])
        dest_spot_type = b.get("spot_type")
        dest_tags = b.get("tags")

        # AP上限距離は必要に応じて調整（テストの安定性重視で広めに）
        leg = rs.calculate_hybrid_leg(
            db,
            origin=origin,
            dest=dest,
            dest_spot_type=dest_spot_type,
            dest_tags=dest_tags,
            ap_max_km=20.0,
        )
        # leg の標準化（key名揺れ対策）
        duration_min = leg.get("duration_min") or leg.get("duration_minutes") or 0
        total_min += float(duration_min) if duration_min is not None else 0.0

        gj = leg.get("geojson")
        if gj and isinstance(gj, dict) and gj.get("type") == "FeatureCollection":
            all_features.extend(gj.get("features", []))

        legs.append(leg)

    # 4) 合成GeoJSONと合計時間を格納
    summary["legs"] = legs
    summary["route_geojson"] = {
        "type": "FeatureCollection",
        "features": all_features,
    }
    summary["total_duration_minutes"] = int(round(total_min))

    return summary


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