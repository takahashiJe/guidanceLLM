# backend/shared/app/schemas.py
# API 入出力で使う Pydantic スキーマ（Gateway / Worker 共有）
from datetime import date
from typing import Optional, List
from pydantic import BaseModel, Field, validator
from __future__ import annotations
from typing import List, Literal, Optional


# ========== Auth ==========
class RegisterRequest(BaseModel):
    username: str = Field(..., description="一意のユーザー名")
    password: str = Field(..., description="パスワード")

class RegisterResponse(BaseModel):
    user_id: str
    username: str

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class RefreshRequest(BaseModel):
    refresh_token: str

class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


# ========== Sessions ==========
class SessionCreateRequest(BaseModel):
    session_id: str = Field(..., description="フロント側で生成した一意ID")

class SessionCreateResponse(BaseModel):
    session_id: str
    current_status: Optional[str] = None
    active_plan_id: Optional[int] = None

class ConversationItem(BaseModel):
    role: str  # "user" / "assistant" / "SYSTEM_TRIGGER" 等
    content: str
    created_at: Optional[str] = None

class SessionRestoreResponse(BaseModel):
    session_id: str
    current_status: Optional[str] = None
    active_plan_id: Optional[int] = None
    history: List[ConversationItem] = []


# ========== Chat / Navigation ==========
class ChatSubmitResponse(BaseModel):
    task_id: str
    accepted: bool = True

class NavigationStartRequest(BaseModel):
    session_id: str
    language: Optional[str] = "ja"

class NavigationLocationRequest(BaseModel):
    session_id: str
    lat: float
    lng: float

class PlanCreateRequest(BaseModel):
    user_id: int
    session_id: str
    start_date: Optional[date] = None
    language: str = Field(default="ja")


class PlanResponse(BaseModel):
    id: int
    user_id: int
    session_id: str
    start_date: Optional[date] = None
    language: str


class StopCreateRequest(BaseModel):
    plan_id: int
    spot_id: int
    position: Optional[int] = None  # None -> 末尾


class StopsReorderRequest(BaseModel):
    plan_id: int
    stop_ids: List[int]  # 新順序（stop.id の配列）


class CongestionStatusResponse(BaseModel):
    spot_id: int
    date: date
    count: int
    status: str

# =========================================================
# Routing 用 追加スキーマ
# =========================================================

OSRMProfile = Literal["car", "foot"]


class Coordinate(BaseModel):
    """(lat, lon) の表現。API レイヤではこのモデルを基本に受け渡しする。"""

    lat: float = Field(..., description="緯度")
    lon: float = Field(..., description="経度")


class GetDistanceAndDurationRequest(BaseModel):
    origin: Coordinate
    destination: Coordinate
    profile: OSRMProfile


class DistanceDurationResponse(BaseModel):
    distance_km: float
    duration_min: float


class FullRouteRequest(BaseModel):
    waypoints: List[Coordinate] = Field(..., min_items=2, description="経由地を含む座標列")
    profile: OSRMProfile
    piston: bool = Field(False, description="ピストン（往復）にする場合は True")


class GeoJSONRouteResponse(BaseModel):
    geojson: dict
    distance_km: float
    duration_min: float


class RerouteRequest(BaseModel):
    current_location: Coordinate
    remaining_waypoints: List[Coordinate] = Field(..., min_items=1)
    profile: OSRMProfile