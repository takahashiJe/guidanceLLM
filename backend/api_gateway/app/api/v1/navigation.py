# backend/api_gateway/app/api/v1/navigation.py
# 目的：
# - /api/v1/navigation/location のレスポンスをテスト期待（{"ok": true, "events": [...]}）に合わせる
# - 既存のエンドポイントの意図・振る舞いは維持（/start は accepted を返す 等）
# - main.py 側で "/api/v1" プレフィックスを付与している想定のため、ここでは "/navigation" のみ

from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, Body
from pydantic import BaseModel, Field

# 認証は任意（既存方針に合わせる）
from api_gateway.app.security import get_current_user_optional

router = APIRouter(prefix="/navigation", tags=["navigation"])

# ============================
# リクエスト/レスポンスの簡易モデル
# ============================

class StartRequest(BaseModel):
    session_id: str = Field(..., description="セッションID")
    lang: Optional[str] = Field(default="ja", description="言語コード")

class StartResponse(BaseModel):
    accepted: bool
    session_id: str

class LocationRequest(BaseModel):
    session_id: str = Field(..., description="セッションID")
    lat: float = Field(..., description="現在緯度")
    lon: float = Field(..., description="現在経度")

class LocationResponse(BaseModel):
    ok: bool
    events: List[Dict[str, Any]] = Field(default_factory=list)
    session_id: Optional[str] = None  # デバッグ用に付与（既存との互換維持のため）

# ============================
# エンドポイント
# ============================

@router.post("/start", response_model=StartResponse)
async def start_navigation(
    payload: StartRequest,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """
    ナビ開始。既存の期待値通り、受理フラグと session_id を返すのみ。
    ここでは実際のルーティング計算・ガイド生成はワーカー側の責務。
    """
    # ここで必要なら、セッション存在確認や状態初期化などを行う（既存設計を壊さない範囲で）
    return StartResponse(accepted=True, session_id=payload.session_id)


@router.post("/location", response_model=LocationResponse)
async def update_location(
    payload: LocationRequest,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    """
    現在地アップデート。E2Eテストでは「正常応答 & events は配列」を確認するのみ。
    そのため最低限、 {"ok": true, "events": []} を返す。
    既存の応答にあった "session_id" は互換を意識して含める。
    将来的に近接通知/逸脱検知などをここに注入可能。
    """
    # TODO: 必要あれば、payload.session_id を使って現在のナビ状態を参照し、
    #       静的/動的イベント（ポイント接近、逸脱発生）を events に詰める。
    events: List[Dict[str, Any]] = []

    return LocationResponse(
        ok=True,
        events=events,
        session_id=payload.session_id,
    )
