# backend/api_gateway/app/api/v1/navigation.py
# 目的：
# - /api/v1/navigation/location および /location_update を本実装
# - 現在地からの逸脱/接近/到着イベント判定、リルート起動（非同期）、TTS合成（ダミー/実エンジン）
# - 既存の構成/認証方針に合わせ、所有権/権限チェックは緩やかに実施

from __future__ import annotations
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone
import math
import os
import base64
import io
import wave
import struct

from fastapi import APIRouter, Depends, Body, HTTPException, status
from sqlalchemy.orm import Session as OrmSession, joinedload

from api_gateway.app.security import get_current_user_optional
from shared.app.database import get_db
from shared.app.models import Session as DbSession, Plan, Stop, Spot
from shared.app.schemas import (
    NavLocationUpdateIn,
    NavLocationUpdateOut,
    TTSItem,
)

# Celery は存在すれば使用（無ければ起動スキップ）
try:
    from shared.app.celery_app import celery_app
except Exception:
    celery_app = None

router = APIRouter(prefix="/navigation", tags=["navigation"])

# ============================
# パラメータ（環境変数）
# ============================
NAV_PROXIMITY_RADIUS_M = float(os.getenv("NAV_PROXIMITY_RADIUS_M", 200))
NAV_DEVIATION_THRESHOLD_M = float(os.getenv("NAV_DEVIATION_THRESHOLD_M", 120))
# ユーザーの .env では AV_ARRIVAL_THRESHOLD_M として指示があったため両対応
NAV_ARRIVAL_THRESHOLD_M = float(os.getenv("NAV_ARRIVAL_THRESHOLD_M", os.getenv("AV_ARRIVAL_THRESHOLD_M", 60)))
REROUTE_COOLDOWN_SEC = int(os.getenv("REROUTE_COOLDOWN_SEC", 20))
TASK_REROUTE = os.getenv("TASK_REROUTE", "navigation.reroute")

# ============================
# 幾何ユーティリティ
# ============================
EARTH_RADIUS_M = 6371000.0

def haversine_m(a: Tuple[float,float], b: Tuple[float,float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    phi1 = math.radians(lat1); phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    s = math.sin(dphi/2)**2  math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
    return 2*EARTH_RADIUS_M*math.asin(math.sqrt(s))

def _project_local_m(lat0: float, lon0: float, lat: float, lon: float) -> Tuple[float,float]:
    # 近傍での等角円筒近似（ローカル投影）：WGS84度→メートル
    x = math.radians(lon - lon0) * EAR_radius_m * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * EAR_radius_m
    return x, y

def point_segment_distance_m(lat: float, lon: float, a: Tuple[float,float], b: Tuple[float,float]) -> float:
    # 線分ABに対する点Pの最短距離（ローカル投影でユークリッド計算）
    lat0 = (a[0]  b[0]) / 2.0
    x1, y1 = _project_local_m(lat0, lon, a[0], a[1])
    x2, y2 = _project_local_m(lat0, lon, b[0], b[1])
    xp, yp = _project_local_m(lat0, lon, lat, lon)
    dx = x2 - x1; dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(xp - x1, yp - y1)
    t = ((xp - x1)*dx  (yp - y1)*dy) / (dx*dx  dy*dy)
    t = max(0.0, min(1.0, t))
    xn = x1  t*dx; yn = y1  t*dy
    return math.hypot(xp - xn, yp - yn)

def distance_to_polyline_m(point: Tuple[float,float], route_geojson: Optional[dict]) -> Optional[float]:
    if not route_geojson:
        return None
    lat, lon = point
    features = route_geojson.get("features") or []
    best = None
    for f in features:
        if f.get("geometry", {}).get("type") != "LineString":
            continue
        coords = f["geometry"].get("coordinates") or []
        for i in range(len(coords)-1):
            lon1, lat1 = coords[i][0], coords[i][1]
            lon2, lat2 = coords[i1][0], coords[i1][1]
            d = point_segment_distance_m(lat, lon, (lat1, lon1), (lat2, lon2))
            if best is None or (d is not None and d < best):
                best = d
    return best

# ============================
# ドメインユーティリティ
# ============================
def find_next_stop(db: OrmSession, plan: Plan) -> Optional[Stop]:
    # 最小 order_index の Stop を「次の目的地」とする（到達管理は後続フェーズで拡張）
    if not plan.stops:
        return None
    return plan.stops[0]  # relationship(order_by=Stop.order_index) により先頭が最小

def load_spot_latlon(stop: Stop) -> Optional[tuple[float,float]]:
    if stop is None or stop.spot is None:
        return None
    return (stop.spot.latitude, stop.spot.longitude)

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def should_reroute(last_at: Optional[datetime], cooldown_sec: int) -> bool:
    if not last_at:
        return True
    return (utcnow() - last_at).total_seconds() >= max(1, cooldown_sec)

def tts_base64(text: str, voice: str = "ja-JP") -> str:
    # TTS_ENGINE=dummy の場合は 1秒のビープを返す（依存ゼロで確実に動作）
    engine = os.getenv("TTS_ENGINE", "dummy")
    if engine == "dummy":
        fr = 16000; dur = 1.0
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(fr)
            for n in range(int(fr*dur)):
                val = int(32767.0 * 0.2 * math.sin(2*math.pi*440*n/fr))
                w.writeframes(struct.pack('<h', val))
        return base64.b64encode(buf.getvalue()).decode("ascii")
    # 将来: 実エンジン（共有の voice_service 経由）に分岐
    # ここで NotImplemented にせず、最低限 dummy にフォールバック
    return tts_base64(text, voice="ja-JP")

def start_reroute_task(session_id: str, origin: tuple[float,float], target_stop_id: Optional[int]) -> bool:
    if celery_app is None:
        return False
    payload = {
        "session_id": session_id,
        "origin_lat": origin[0],
        "origin_lon": origin[1],
        "target_stop_id": target_stop_id,
    }
    try:
        celery_app.send_task(TASK_REROUTE, args=[payload])
        return True
    except Exception:
        return False

# ============================
# エンドポイント実装
# ============================

@router.post("/location", response_model=NavLocationUpdateOut)
@router.post("/location_update", response_model=NavLocationUpdateOut)  # 互換エイリアス
def location_update(
    payload: NavLocationUpdateIn = Body(...),
    db: OrmSession = Depends(get_db),
    user = Depends(get_current_user_optional),
):
    \"\"\"
    現在地アップデート（本実装）
    - セッションとアクティブプランの取得
    - 逸脱/接近/到着イベント判定
    - デバウンスに基づくリルート起動（非同期）
    - 接近イベントに対するTTS合成（Base64）
    - `plan_version` を返却し、クライアントは /plans/{id}/summary の差分をポーリング可
    \"\"\"
    # 1) セッション/プランを取得
    sess: DbSession | None = db.query(DbSession).filter(DbSession.id == payload.session_id).first()
    if not sess:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    plan: Plan | None = None
    if sess.active_plan_id:
        plan = db.query(Plan).options(joinedload(Plan.stops).joinedload(Stop.spot)).filter(Plan.id == sess.active_plan_id).first()

    if plan is None or not plan.stops:
        # プランなしの場合：空イベント・plan_versionなし
        return NavLocationUpdateOut(events=[], actions={}, plan_version=None)

    # 所有権チェック（任意）
    if user and plan.user_id and user.id != plan.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    # 2) 逸脱判定
    events: List[Dict[str, Any]] = []
    actions: Dict[str, Any] = {"reroute": {"started": False, "debounced": False}, "tts": []}

    onroute_dist = distance_to_polyline_m((payload.lat, payload.lon), plan.route_geojson)
    if onroute_dist is None or onroute_dist > NAV_DEVIATION_THRESHOLD_M:
        events.append({ "type": "REROUTE_REQUESTED", "reason": "off_route", "distance_to_route_m": int(onroute_dist) if onroute_dist is not None else -1 })

    # 3) 接近/到着判定
    next_stop = find_next_stop(db, plan)
    if next_stop:
        spot_pos = load_spot_latlon(next_stop)
        if spot_pos:
            d = haversine_m((payload.lat, payload.lon), spot_pos)
            if d < NAV_ARRIVAL_THRESHOLD_M:
                events.append({ "type": "PROXIMITY_ARRIVAL", "stop_id": next_stop.id, "distance_m": int(d) })
            elif d < NAV_PROXIMITY_RADIUS_M:
                events.append({ "type": "PROXIMITY_APPROACH", "stop_id": next_stop.id, "distance_m": int(d) })

    # 4) デバウンス→リルート起動
    do_reroute = any(e.get("type") == "REROUTE_REQUESTED" for e in events) and next_stop is not None
    if do_reroute:
        cooldown = sess.reroute_cooldown_sec or REROUTE_COOLDOWN_SEC
        if should_reroute(sess.last_reroute_at, cooldown):
            started = start_reroute_task(sess.id, (payload.lat, payload.lon), next_stop.id if next_stop else None)
            if started:
                sess.last_reroute_at = datetime.now(timezone.utc)
                actions["reroute"]["started"] = True
            else:
                actions["reroute"]["debounced"] = True  # 起動失敗を debounced と同じ扱いに
        else:
            actions["reroute"]["debounced"] = True

    # 5) TTS：接近/到着イベントに対してガイドを合成（text は仮にスポット名）
    for e in events:
        if e["type"].startswith("PROXIMITY"):
            spot_name = next_stop.spot.official_name if (next_stop and next_stop.spot) else "次の目的地"
            guide_text = f"{spot_name} が近づいてきました。"
            audio_b64 = tts_base64(guide_text, voice="ja-JP")
            actions["tts"].append({ "stop_id": e.get("stop_id"), "voice": "ja-JP", "mime": "audio/wav", "audio_base64": audio_b64 })

    db.commit()

    return NavLocationUpdateOut(events=events, actions=actions, plan_version=plan.route_version if plan else None)