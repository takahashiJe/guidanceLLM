# -*- coding: utf-8 -*-
"""
共有Celeryタスク定義（Gateway/Worker 双方でインポートされるモジュール）
- ここに「タスク名の定数」を定義して、Gateway 側が Celery に投げる際のシグネチャとして使う
- 既存のタスク（マテビュー更新、Routing I/F）も保持
- Worker 側の実体タスク名と一致させること
"""

from __future__ import annotations

import os
from typing import Any, Dict
from datetime import date

from sqlalchemy import create_engine, text

from shared.app.celery_app import celery_app
from shared.app.database import SessionLocal

# =========================================================
# タスク名の定数（Gateway/Worker で共有）
#  - Worker 側の @celery_app.task(name=...) と一致させる
# =========================================================
TASK_ORCHESTRATE_CONVERSATION = "worker.app.tasks.orchestrate_conversation_task"
TASK_START_NAVIGATION = "worker.app.tasks.navigation_start_task"
TASK_UPDATE_LOCATION = "worker.app.tasks.navigation_location_update_task"
TASK_PREGENERATE_GUIDES = "worker.app.tasks.pregenerate_guides_task"

__all__ = [
    "TASK_ORCHESTRATE_CONVERSATION",
    "TASK_START_NAVIGATION",
    "TASK_UPDATE_LOCATION",
    "TASK_PREGENERATE_GUIDES",
    "refresh_spot_congestion_mv",
    "refresh_congestion_mv_task",
    "routing_get_distance_and_duration",
    "routing_calculate_full_itinerary_route",
    "routing_calculate_reroute",
]

# =========================================================
# 既存のヘルス/メンテ系タスク
# =========================================================

DB_URL = os.getenv("DATABASE_URL")
_engine = create_engine(DB_URL, future=True)


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


# =========================================================
# Routing 用 追加タスク（既存I/Fを維持）
# =========================================================

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
