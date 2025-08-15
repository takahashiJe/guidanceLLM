# backend/shared/app/schemas.py
# API 入出力で使う Pydantic スキーマ（Gateway / Worker 共有）
from datetime import date
from typing import Optional, List
from pydantic import BaseModel, Field


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