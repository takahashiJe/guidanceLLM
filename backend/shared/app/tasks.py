# -*- coding: utf-8 -*-
"""
共有Celeryタスク定義 / タスクフォワーダ
- API ノードから Worker 専用タスクを直接 import しないためのフォワーダを提供
- ここにマテビュー更新タスクや軽量タスクを配置
- 既存のタスク名・インポートは壊さない（routing など）
"""

import os
from typing import Dict, Any
from celery.result import AsyncResult
from sqlalchemy import create_engine, text

from shared.app.celery_app import celery_app
from shared.app.database import SessionLocal

# ---------------------------------------------------------------------
# DB 接続（マテビュー更新用）
# ---------------------------------------------------------------------
DB_URL = os.getenv("DATABASE_URL")
_engine = create_engine(DB_URL, future=True)


# ---------------------------------------------------------------------
# フォワーダ：オーケストレーション
# ---------------------------------------------------------------------
def orchestrate_message(payload: Dict[str, Any]) -> AsyncResult:
    """
    API から呼ばれる「フォワーダ」関数。
    Worker 側の 'worker.app.tasks.orchestrate_message' タスクへ委譲する。
    - API ノードで Worker 実装を import しないために send_task を使う
    - 戻り値は AsyncResult（フロントはポーリング運用なのでIDだけ使えばOK）
    """
    return celery_app.send_task("worker.app.tasks.orchestrate_message", args=[payload])


# ---------------------------------------------------------------------
# マテリアライズド・ビュー更新系
# ---------------------------------------------------------------------
@celery_app.task(name="shared.app.tasks.refresh_spot_congestion_mv")
def refresh_spot_congestion_mv():
    """
    ルーティンで spot_congestion_mv を更新するタスク。
    初回は CONCURRENTLY が使えない可能性があるため通常 REFRESH にフォールバック。
    """
    with _engine.begin() as conn:
        try:
            conn.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY spot_congestion_mv;"))
        except Exception:
            conn.execute(text("REFRESH MATERIALIZED VIEW spot_congestion_mv;"))


@celery_app.task(name="worker.app.tasks.refresh_congestion_mv_task")
def refresh_congestion_mv_task() -> str:
    """
    マテビュー 'congestion_by_date_spot' を CONCURRENTLY でリフレッシュ。
    - 事前にユニークインデックスが必要（init_db_script で作成）
    """
    mv = "congestion_by_date_spot"
    with SessionLocal() as db:
        try:
            db.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv};"))
            db.commit()
            return "ok"
        except Exception as e:
            db.rollback()
            # MV未作成などの場合はログのみ（初回ブート順の差異考慮）
            return f"failed: {e}"


# ---------------------------------------------------------------------
# Routing 用 追加タスク
# ---------------------------------------------------------------------
@celery_app.task(name="routing.get_distance_and_duration", bind=True)
def routing_get_distance_and_duration(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    入力 payload:
      {
        "origin": {"lat": float, "lon": float},
        "destination": {"lat": float, "lon": float},
        "profile": "car" | "foot"
      }
    戻り値:
      {"distance_km": float, "duration_min": float}
    """
    # 遅延 import（API 側でインポートされても失敗しないように）
    from worker.app.services.routing.routing_service import RoutingService

    svc = RoutingService()
    origin = (float(payload["origin"]["lat"]), float(payload["origin"]["lon"]))
    destination = (float(payload["destination"]["lat"]), float(payload["destination"]["lon"]))
    profile = payload["profile"]

    result = svc.get_distance_and_duration(origin, destination, profile)
    return result


@celery_app.task(name="routing.calculate_full_itinerary_route", bind=True)
def routing_calculate_full_itinerary_route(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    入力 payload:
      {
        "waypoints": [{"lat": float, "lon": float}, ...],  # 2点以上
        "profile": "car" | "foot",
        "piston": bool
      }
    戻り値:
      {"geojson": dict, "distance_km": float, "duration_min": float}
    """
    from worker.app.services.routing.routing_service import RoutingService

    svc = RoutingService()
    waypoints = [(float(c["lat"]), float(c["lon"])) for c in payload["waypoints"]]
    profile = payload["profile"]
    piston = bool(payload.get("piston", False))

    result = svc.calculate_full_itinerary_route(waypoints, profile, piston=piston)
    return result


@celery_app.task(name="routing.calculate_reroute", bind=True)
def routing_calculate_reroute(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    入力 payload:
      {
        "current_location": {"lat": float, "lon": float},
        "remaining_waypoints": [{"lat": float, "lon": float}, ...],  # 1点以上
        "profile": "car" | "foot"
      }
    戻り値:
      {"geojson": dict, "distance_km": float, "duration_min": float}
    """
    from worker.app.services.routing.routing_service import RoutingService

    svc = RoutingService()
    current_location = (float(payload["current_location"]["lat"]), float(payload["current_location"]["lon"]))
    remaining_waypoints = [(float(c["lat"]), float(c["lon"])) for c in payload["remaining_waypoints"]]
    profile = payload["profile"]

    result = svc.calculate_reroute(current_location, remaining_waypoints, profile)
    return result
