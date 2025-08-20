# backend/api_gateway/app/api/v1/navigation.py
# [CHANGED] 役割分離：APIを“薄く”。イベント判定は shared の純関数に移動し、リルート計算は Celery 経由で Worker に依頼します。
# [KEPT]     既存エンドポイント構成（/navigation/location および /location_update の互換）、レスポンスに plan_version を含める仕様は維持。
# [ADDED]    デバウンス（DBの last_reroute_at / reroute_cooldown_sec）と Celery 起動、TTS の同期生成（dummy）を実装。

from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone
import os
import base64
import io
import math
import struct
import wave

from fastapi import APIRouter, Depends, Body, HTTPException, status
from sqlalchemy.orm import Session as OrmSession, joinedload

from api_gateway.app.security import get_current_user_optional
from shared.app.database import get_db
from shared.app.models import Session as DbSession, Plan, Stop
from shared.app.schemas import (
    NavLocationUpdateIn,
    NavLocationUpdateOut,
)
# [ADDED] イベント判定の純関数（API/Worker 共有）
from shared.app.services.navigation_events import evaluate_events, Thresholds

from shared.app.tasks import enqueue_reroute

router = APIRouter(prefix="/navigation", tags=["navigation"])

# ---------------------------
# 環境変数（閾値）
# ---------------------------
NAV_PROXIMITY_RADIUS_M = float(os.getenv("NAV_PROXIMITY_RADIUS_M", 200))
NAV_DEVIATION_THRESHOLD_M = float(os.getenv("NAV_DEVIATION_THRESHOLD_M", 120))
# .envで AV_ARRIVAL_THRESHOLD_M を追加した旨があったため両対応
NAV_ARRIVAL_THRESHOLD_M = float(os.getenv("NAV_ARRIVAL_THRESHOLD_M", os.getenv("AV_ARRIVAL_THRESHOLD_M", 60)))
REROUTE_COOLDOWN_SEC = int(os.getenv("REROUTE_COOLDOWN_SEC", 20))
TASK_REROUTE = os.getenv("TASK_REROUTE", "navigation.reroute")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def should_reroute(last_at: Optional[datetime], cooldown_sec: int) -> bool:
    """[KEPT] デバウンスの基本ロジックはこれまでの方針通り。"""
    if not last_at:
        return True
    return (utcnow() - last_at).total_seconds() >= max(1, cooldown_sec)


def synthesize_tts_base64(text: str, voice: str = "ja-JP") -> str:
    """
    [KEPT/ADDED] テストで確実に鳴る dummy TTS（1秒ビープ）。本番では voice_service への差し替えでOK。
    APIで同期的に返す要件のため、ここに小さな実装を保持しています。
    """
    engine = os.getenv("TTS_ENGINE", "dummy")
    if engine != "dummy":
        # TODO: 共有の voice_service へ差し替え可能（将来）
        pass

    fr = 16000
    dur = 1.0
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fr)
        for n in range(int(fr * dur)):
            val = int(32767.0 * 0.2 * math.sin(2 * math.pi * 440 * n / fr))
            w.writeframes(struct.pack("<h", val))
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _enqueue_reroute(session_id: str, origin_lat: float, origin_lon: float,
                     target_stop_id: Optional[int], base_route_version: Optional[int]) -> bool:
    """[ADDED] Celery タスク起動。ブローカー未接続などで失敗したら False を返す。"""
    if celery_app is None:
        return False
    payload = {
        "session_id": session_id,
        "origin_lat": origin_lat,
        "origin_lon": origin_lon,
        "target_stop_id": target_stop_id,
        "base_route_version": base_route_version,
    }
    try:
        enqueue_reroute(
            session_id=sess.id,
            origin_lat=payload.lat,
            origin_lon=payload.lon,
            target_stop_id=next_stop.id if next_stop else None,
            base_route_version=plan.route_version,
        )
        return True
    except Exception:
        return False


@router.post("/location", response_model=NavLocationUpdateOut)
@router.post("/location_update", response_model=NavLocationUpdateOut)
def location_update(
    payload: NavLocationUpdateIn = Body(...),
    db: OrmSession = Depends(get_db),
    user=Depends(get_current_user_optional),
):
    """
    [CHANGED] 現在地アップデート本実装。
      - [薄型化] イベント判定は shared の純関数 evaluate_events に委譲（APIからはDB読取のみ）。
      - [保持] plan_version の返却、TTS音声をレスポンスへ同梱。
      - [追加] Celery でのリルート起動（楽観ロックのベース版も渡す）。
    """
    # 1) セッションとアクティブプランを取得
    sess: Optional[DbSession] = db.query(DbSession).filter(DbSession.id == payload.session_id).first()
    if not sess:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")

    plan: Optional[Plan] = None
    if sess.active_plan_id:
        plan = (
            db.query(Plan)
            .options(joinedload(Plan.stops).joinedload(Stop.spot))
            .filter(Plan.id == sess.active_plan_id)
            .first()
        )

    if plan is None or not plan.stops:
        # [KEPT] プランがない場合は空で返す
        return NavLocationUpdateOut(events=[], actions={}, plan_version=None)

    # 所有権チェック（既存方針に合わせ、user が存在し plan に所有者がいる場合は照合）
    if user and plan.user_id and user.id != plan.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    # 2) イベント判定（純関数）
    th = Thresholds(
        off_route_m=NAV_DEVIATION_THRESHOLD_M,
        approach_m=NAV_PROXIMITY_RADIUS_M,
        arrival_m=NAV_ARRIVAL_THRESHOLD_M,
    )
    events, next_stop, offroute_distance = evaluate_events(
        current=(payload.lat, payload.lon),
        plan=plan,
        thresholds=th,
    )

    actions: Dict[str, Any] = {"reroute": {"started": False, "debounced": False}, "tts": []}

    # 3) リルート起動（デバウンス）
    do_reroute = any(e.get("type") == "REROUTE_REQUESTED" for e in events) and next_stop is not None
    if do_reroute:
        cooldown = sess.reroute_cooldown_sec or REROUTE_COOLDOWN_SEC
        if should_reroute(sess.last_reroute_at, cooldown):
            started = enqueue_reroute(
                session_id=sess.id,
                origin_lat=payload.lat,
                origin_lon=payload.lon,
                target_stop_id=next_stop.id if next_stop else None,
                base_route_version=plan.route_version,
            )
            if started:
                sess.last_reroute_at = utcnow()
                actions["reroute"]["started"] = True
            else:
                actions["reroute"]["debounced"] = True  # 起動失敗時は抑止扱い
        else:
            actions["reroute"]["debounced"] = True

    # 4) TTS：接近/到着イベントに対して合成（ガイド文は簡易にスポット名）
    for e in events:
        if e["type"].startswith("PROXIMITY"):
            spot_name = next_stop.spot.official_name if (next_stop and next_stop.spot) else "次の目的地"
            guide_text = f"{spot_name} が近づいてきました。"
            audio_b64 = synthesize_tts_base64(guide_text, voice="ja-JP")
            actions["tts"].append(
                {"stop_id": e.get("stop_id"), "voice": "ja-JP", "mime": "audio/wav", "audio_base64": audio_b64}
            )

    db.commit()

    # 5) 応答
    return NavLocationUpdateOut(
        events=events,
        actions=actions,
        plan_version=plan.route_version if plan else None,
    )
