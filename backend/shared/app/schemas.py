# backend/shared/app/schemas.py
# API 入出力で使う Pydantic スキーマ（Gateway / Worker 共有）
from __future__ import annotations
from datetime import date, datetime
from typing import Optional, List
from pydantic import BaseModel, Field, validator
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


class STTRequest(BaseModel):
    """STT 用の入力ペイロード（API や Celery タスク間で使う）"""
    session_id: str
    audio_b64: str  # base64 エンコードされた音声
    lang: Optional[Literal["ja", "en", "zh"]] = None


class STTResult(BaseModel):
    """STT の出力（文+メタ）"""
    text: str
    detected_language: Optional[str] = None
    duration: Optional[float] = None
    language_probability: Optional[float] = None


class TTSRequest(BaseModel):
    """TTS 用の入力ペイロード"""
    session_id: str
    text: str
    lang: Literal["ja", "en", "zh"]


class TTSResult(BaseModel):
    """TTS の出力（base64 WAV とメタ）"""
    audio_b64: str
    sample_rate: int = Field(default=22050)
    lang: Literal["ja", "en", "zh"]

# =========================================================
# Conversation Embedding 用スキーマ
# =========================================================

class ConversationEmbeddingCreate(BaseModel):
    session_id: str
    speaker: str  # 'user'|'assistant'|'system'
    lang: Optional[str] = None
    ts: datetime = Field(default_factory=datetime.utcnow)
    text: str
    embedding: List[float]
    embedding_version: str = "mxbai-embed-large"


class ConversationEmbeddingRead(BaseModel):
    id: int
    session_id: str
    speaker: str
    lang: Optional[str] = None
    ts: datetime
    text: str
    embedding_version: str

    class Config:
        from_attributes = True