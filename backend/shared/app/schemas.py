# shared/app/schemas.py

from typing import TypedDict, List, Optional, Any
from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime

# ==============================================================================
# LangGraph Agent State
# ==============================================================================
class AgentState(TypedDict):
    """
    LangGraphのグラフ全体で受け渡される状態オブジェクト。
    FR-1, FR-2, FR-6, FR-7の要件を管理する。
    """
    # === セッション情報 ===
    # FR-1-1: 認証されたユーザーのID
    userId: int
    
    # FR-1-2: 現在の会話セッションのID
    sessionId: str
    
    # FR-6-1: ユーザーが選択した言語 ('ja', 'en', 'zh')
    language: str

    # FR-7-3: 現在の対話モード ('text' or 'voice')
    interactionMode: str
    
    # FR-1-3: 現在のアプリの状態 ('Browse', 'planning', 'navigating')
    appStatus: str
    
    # === 対話情報 ===
    # FR-2-2: LLMに渡す直近の会話履歴
    chatHistory: List[BaseMessage]

    # フロントエンドに返却する、AIの最終応答テキスト
    finalResponse: str

    # === 計画情報 ===
    # FR-4: 現在編集中の周遊計画ID
    activePlanId: Optional[int]
    
    # --- 内部処理用 ---
    
    # ノード間で受け渡す一時的なデータ
    # (例: Agentic RAGで収集した情報、推薦候補スポットリストなど)
    intermediateData: dict

# ==============================================================================
# 共通スキーマ
# ==============================================================================

class Location(BaseModel):
    """ユーザーの現在地座標を定義する共通スキーマ"""
    latitude: float
    longitude: float

# ==============================================================================
# 認証 (Authentication) 関連スキーマ
# ==============================================================================

class UserBase(BaseModel):
    """ユーザー情報の基本スキーマ"""
    username: str

class UserCreate(UserBase):
    """[POST /register] 新規ユーザー登録時のリクエストボディ"""
    password: str

class UserResponse(UserBase):
    """[POST /register] 新規ユーザー登録成功時のレスポンスボディ"""
    user_id: int

    class Config:
        orm_mode = True

class Token(BaseModel):
    """[POST /login] ログイン成功時のレスポンスボディ"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class RefreshToken(BaseModel):
    """[POST /token/refresh] トークン更新時のリクエストボディ"""
    refresh_token: str

class AccessToken(BaseModel):
    """[POST /token/refresh] トークン更新成功時のレスポンスボディ"""
    access_token: str
    token_type: str = "bearer"

class TokenData(BaseModel):
    """JWTトークンのペイロード（内容）を検証するための内部利用スキーマ"""
    username: Optional[str] = None


# ==============================================================================
# セッション (Session) 関連スキーマ
# ==============================================================================

class SessionCreate(BaseModel):
    """[POST /sessions/create] 新規セッション作成時のリクエストボディ"""
    session_id: UUID
    user_id: int
    language: str
    interaction_mode: str

class SessionResponse(BaseModel):
    """[POST /sessions/create] 新規セッション作成成功時のレスポンスボディ"""
    session_id: UUID
    user_id: int

    class Config:
        orm_mode = True

class ConversationHistoryResponse(BaseModel):
    """会話履歴のレスポンス形式 (セッション復元時に使用)"""
    turn: int
    user_input: Optional[str]
    ai_output: Optional[str]
    created_at: datetime

    class Config:
        orm_mode = True
        
class SessionRestoreResponse(BaseModel):
    """[GET /sessions/restore/{session_id}] セッション復元成功時のレスポンスボディ"""
    session_id: UUID
    user_id: int
    app_status: str
    active_plan_id: Optional[int]
    language: str
    interaction_mode: str
    history: List[ConversationHistoryResponse]


# ==============================================================================
# 対話 (Chat) 関連スキーマ
# ==============================================================================

class ChatRequest(BaseModel):
    """[POST /chat/] チャットリクエストのリクエストボディ"""
    session_id: UUID
    user_message: str

class ChatResponse(BaseModel):
    """[POST /chat/] チャットリクエスト受付成功時のレスポンスボディ"""
    message: str
    task_id: str

# ==============================================================================
# ナビゲーション (Navigation) 関連スキーマ
# ==============================================================================

class NavigationStart(BaseModel):
    """[POST /navigation/start] ナビゲーション開始時のリクエストボディ"""
    session_id: UUID
    plan_id: int

class LocationData(BaseModel):
    """[POST /navigation/location] 位置情報更新時のリクエストボディ"""
    session_id: UUID
    location: Location