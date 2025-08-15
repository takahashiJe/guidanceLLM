# backend/shared/app/schemas.py
from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, Field, EmailStr

# ===== Auth =====

class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str
    preferred_language: Optional[str] = "ja"

class LoginRequest(BaseModel):
    username: str
    password: str

class TokenPair(BaseModel):
    access_token: str
    refresh_token: str

class AccessToken(BaseModel):
    access_token: str
    # ローテーションの都合で新 refresh も返す
    refresh_token: Optional[str] = None

class TokenRefreshRequest(BaseModel):
    refresh_token: str

# ===== Sessions =====

class SessionCreateRequest(BaseModel):
    session_id: str
    language: Optional[str] = "ja"
    dialogue_mode: Optional[str] = "text"  # text / voice

class SessionCreateResponse(BaseModel):
    session_id: str
    current_status: str

class ChatMessage(BaseModel):
    role: str  # user / assistant / system_trigger
    content: str
    created_at: datetime
    meta: Optional[dict] = None

class SessionRestoreResponse(BaseModel):
    session_id: str
    current_status: str
    active_plan_id: Optional[int] = None
    language: str
    dialogue_mode: str
    history: List[ChatMessage] = []

# ===== Chat / Navigation =====

class TaskAcceptedResponse(BaseModel):
    task_id: str

class NavigationStartRequest(BaseModel):
    session_id: str

class NavigationLocationUpdate(BaseModel):
    session_id: str
    lat: float
    lon: float
    heading: Optional[float] = None
    speed: Optional[float] = None
