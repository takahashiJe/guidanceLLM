# -*- coding: utf-8 -*-
"""
共有Celeryタスク定義 & タスク名の定数化。

目的:
- API Gateway / Worker の双方で Celery タスク名を一元管理（定数）する。
- 既存の処理（MVリフレッシュ / Routing 計算）を維持しつつ、名前の衝突を回避。
- Worker プロセスからもこのモジュールが読み込まれる想定。

注意:
- Celery ワーカー起動コマンド: 
    celery -A shared.app.celery_app.celery_app worker --loglevel=info --pool=threads --concurrency=1
- 本ファイルに定義するタスクは「共有タスク（どこからでも呼ばれる）」のみ。
  音声/STT・TTS やオーケストレーションは Worker 側に定義（本ファイルは定数のみ提供）。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple, Optional

from sqlalchemy import create_engine, text

from shared.app.celery_app import celery_app

# =========================================================
# タスク名の定数（ここを唯一の真実源にする）
# =========================================================

# --- Orchestration / Guides / Navigation 起動 ---
TASK_ORCHESTRATE_CONVERSATION: str = "orchestration.orchestrate_conversation"
TASK_PREGENERATE_GUIDES: str = "orchestration.pregenerate_guides"
TASK_START_NAVIGATION: str = "navigation.start"
TASK_UPDATE_LOCATION: str = "navigation.location_update"

# --- Voice (STT/TTS) ---
TASK_STT_TRANSCRIBE: str = "voice.stt_transcribe"
TASK_TTS_SYNTHESIZE: str = "voice.tts_synthesize"

# --- Routing ---
TASK_ROUTING_GET_DISTANCE_AND_DURATION: str = "routing.get_distance_and_duration"
TASK_ROUTING_CALC_FULL_ITINERARY: str = "routing.calculate_full_itinerary_route"
TASK_ROUTING_CALCULATE_REROUTE: str = "routing.calculate_reroute"

# --- Maintenance / Materialized View Refresh ---
# 既存互換のため、名称は従来のものを維持（他所から参照されている可能性がある）
TASK_REFRESH_SPOT_CONGESTION_MV: str = "shared.app.tasks.refresh_spot_congestion_mv"
TASK_REFRESH_CONGESTION_MV: str = "worker.app.tasks.refresh_congestion_mv_task"

# =========================================================
# DB 接続（MV 更新系で使用）
# =========================================================

DB_URL = os.getenv("DATABASE_URL")
_engine = create_engine(DB_URL, future=True)


# =========================================================
# Maintenance: マテビュー更新タスク
# =========================================================

@celery_app.task(name=TASK_REFRESH_SPOT_CONGESTION_MV)
def refresh_spot_congestion_mv() -> str:
    """
    ルーティンで 'spot_congestion_mv' を更新するタスク。
    初回は CONCURRENTLY が使えない可能性があるため通常 REFRESH にフォールバック。
    戻り値: "ok" / "fallback" / "failed: <error>"
    """
    with _engine.begin() as conn:
        try:
            conn.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY spot_congestion_mv;"))
            return "ok"
        except Exception:
            try:
                conn.execute(text("REFRESH MATERIALIZED VIEW spot_congestion_mv;"))
                return "fallback"
            except Exception as e:
                return f"failed: {e}"


@celery_app.task(name=TASK_REFRESH_CONGESTION_MV)
def refresh_congestion_mv_task() -> str:
    """
    マテビュー 'congestion_by_date_spot' を CONCURRENTLY でリフレッシュ。
    - 事前にユニークインデックスが必要（init_db_script / Alembic で作成済みの想定）
    戻り値: "ok" / "failed: <error>"
    """
    with _engine.begin() as conn:
        try:
            conn.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY congestion_by_date_spot;"))
            return "ok"
        except Exception as e:
            return f"failed: {e}"


# =========================================================
# Routing: OSRM 専念タスク（Worker 側の RoutingService に委譲）
#   ※ shared で定義する理由:
#     - Gateway 側から直接このシグネチャでディスパッチするため
#     - Worker が本 shared を読み込み、実体は Worker 内サービスに委譲される
# =========================================================

@celery_app.task(name=TASK_ROUTING_GET_DISTANCE_AND_DURATION, bind=True)
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
    # 遅延 import（API 側プロセスで import されても失敗しないように）
    from worker.app.services.routing.routing_service import RoutingService  # type: ignore

    svc = RoutingService()
    origin = (float(payload["origin"]["lat"]), float(payload["origin"]["lon"]))
    destination = (float(payload["destination"]["lat"]), float(payload["destination"]["lon"]))
    profile = payload["profile"]

    result = svc.get_distance_and_duration(origin, destination, profile)
    return result


@celery_app.task(name=TASK_ROUTING_CALC_FULL_ITINERARY, bind=True)
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
    from worker.app.services.routing.routing_service import RoutingService  # type: ignore

    svc = RoutingService()
    waypoints = [(float(c["lat"]), float(c["lon"])) for c in payload["waypoints"]]
    profile = payload["profile"]
    piston = bool(payload.get("piston", False))

    result = svc.calculate_full_itinerary_route(waypoints, profile, piston=piston)
    return result


@celery_app.task(name=TASK_ROUTING_CALCULATE_REROUTE, bind=True)
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
    from worker.app.services.routing.routing_service import RoutingService  # type: ignore

    svc = RoutingService()
    current_location = (float(payload["current_location"]["lat"]), float(payload["current_location"]["lon"]))
    remaining_waypoints = [(float(c["lat"]), float(c["lon"])) for c in payload["remaining_waypoints"]]
    profile = payload["profile"]

    result = svc.calculate_reroute(current_location, remaining_waypoints, profile)
    return result
