# -*- coding: utf-8 -*-
"""
/api/v1/navigation エンドポイント群
- ナビ開始（ガイド事前生成トリガ）
- 必要になれば今後 /location/update も追加可能
"""

from __future__ import annotations

import os
from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from shared.app.celery_app import celery_app
from shared.app import models
from api_gateway.app.security import get_current_user_optional

# Celery タスク名（Worker 側と一致させる）
TASK_START_NAVIGATION = os.getenv("TASK_START_NAVIGATION", "navigation.start")

router = APIRouter(prefix="/navigation", tags=["navigation"])


@router.post("/start")
async def navigation_start(
    body: Dict[str, Any],
    current_user: Optional[models.User] = Depends(get_current_user_optional),
):
    """
    ナビゲーション開始。ガイド文の事前生成などを Worker 側で実行。
    入力: { "session_id": str, "lang": "ja"|"en"|"zh" }
    返却: 202 + {task_id}
    """
    session_id = body.get("session_id")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    lang = (body.get("lang") or "ja").strip()
    user_id = current_user.id if isinstance(current_user, models.User) else None

    payload = {"session_id": session_id, "lang": lang, "user_id": user_id}

    try:
        ar = celery_app.send_task(TASK_START_NAVIGATION, args=[payload])
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"failed to enqueue task: {e}")

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"accepted": True, "session_id": session_id, "task_id": ar.id},
    )
