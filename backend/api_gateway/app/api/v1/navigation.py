# -*- coding: utf-8 -*-
# backend/api_gateway/app/api/v1/navigation.py
"""
ナビゲーション関連API。
- /api/v1/navigation/start    : ナビ開始（ガイド事前生成などをトリガー）
- /api/v1/navigation/location : 位置更新（逸脱/接近イベントのトリガー）
いずれも Celery に委譲し、基本は 202 Accepted を返す。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from starlette.responses import JSONResponse

from api_gateway.app.security import get_current_user_optional
from shared.app.celery_app import celery_app
from shared.app.tasks import TASK_START_NAVIGATION, TASK_UPDATE_LOCATION

router = APIRouter(prefix="/navigation")


def _extract_optional_user_id(current_user) -> Optional[int]:
    if not current_user:
        return None
    if hasattr(current_user, "id"):
        return int(current_user.id)
    if isinstance(current_user, dict) and "user_id" in current_user:
        return int(current_user["user_id"])
    return None


@router.post("/start")
async def start_navigation(body: dict, current_user=Depends(get_current_user_optional)):
    """
    ナビ開始トリガー。
    body: { "session_id": str, "lang": "ja"|"en"|"zh" }
    """
    session_id: str = (body or {}).get("session_id")
    lang: str = (body or {}).get("lang") or "ja"
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    payload = {
        "session_id": session_id,
        "user_id": _extract_optional_user_id(current_user),
        "lang": lang,
    }
    celery_app.send_task(TASK_START_NAVIGATION, args=[payload])

    # 受理のみ（テストは 200/202 を許容）
    return JSONResponse(status_code=202, content={"accepted": True, "session_id": session_id})


@router.post("/location")
async def update_location(body: dict, current_user=Depends(get_current_user_optional)):
    """
    位置情報アップデート。
    body: { "session_id": str, "lat": float, "lon": float }
    """
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid body")
    session_id = body.get("session_id")
    lat = body.get("lat")
    lon = body.get("lon")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    if lat is None or lon is None:
        raise HTTPException(status_code=400, detail="lat/lon are required")

    payload = {
        "session_id": session_id,
        "user_id": _extract_optional_user_id(current_user),
        "lat": float(lat),
        "lon": float(lon),
    }
    # 受理して即返す（重い処理は Worker 側）
    celery_app.send_task(TASK_UPDATE_LOCATION, args=[payload])
    return JSONResponse(status_code=202, content={"accepted": True, "session_id": session_id})
