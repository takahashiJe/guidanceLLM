# -*- coding: utf-8 -*-
"""
認証・ユーザー管理エンドポイント
- POST /api/v1/auth/register
- POST /api/v1/auth/login
- POST /api/v1/auth/token/refresh

要点:
- ルーターの prefix は "/auth"（/api/v1 は main.py 側で付与）
- DB モデルに email カラムが無い前提で、email は User.username に保存・検索する
- /register は
    1) {"email", "password"} 形式（E2E）
    2) {"username"(=メール文字列), "password", "display_name"} 形式（ユニット）
  の両対応
- Pydantic v2: EmailStr 検証は TypeAdapter を使用
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, status, Body
from pydantic import BaseModel, EmailStr, Field, TypeAdapter, ValidationError, field_validator
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from api_gateway.app.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,  # refresh 検証は decode で行う
)
from shared.app.database import get_db
from shared.app import models

import os
try:
    from jose import jwt  # 既存依存のはず（common pattern）
except Exception:
    # jose が無い環境はほぼ無い想定だが、無いなら requirements に追加する
    raise

JWT_SECRET = os.getenv("JWT_SECRET", os.getenv("JWT_SECRET_KEY", "dev-secret"))
JWT_ALG    = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_MIN = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
REFRESH_D  = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

def _encode_jwt_for_user(user_id: int | str, ttl: timedelta, typ: str) -> str:
    now = datetime.utcnow()
    payload = {
        "sub": str(user_id),
        "type": typ,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def issue_access_token(user_id: int | str) -> str:
    return _encode_jwt_for_user(user_id, timedelta(minutes=ACCESS_MIN), "access")

def issue_refresh_token(user_id: int | str) -> str:
    return _encode_jwt_for_user(user_id, timedelta(days=REFRESH_D), "refresh")

def _issue_access_token_for(user_id: int) -> str:
    # 既存ユーティリティの名前/シグネチャ揺れに耐性を持たせる
    try:
        return issue_access_token(user_id)  # 新名
    except NameError:
        # 古い関数名
        try:
            return create_access_token(user_id=user_id)
        except TypeError:
            return create_access_token(user_id)  # 位置引数版

def _issue_refresh_token_for(user_id: int) -> str:
    try:
        return issue_refresh_token(user_id)
    except NameError:
        try:
            return create_refresh_token(user_id=user_id)
        except TypeError:
            return create_refresh_token(user_id)


# ------------------------------------------------------------
# 入出力スキーマ（shared を壊さないためにローカル定義）
# ------------------------------------------------------------

class RegisterRequest(BaseModel):
    """
    /register で受け付ける柔軟な入力スキーマ。
    - email/password だけ（E2E）
    - username/password(+display_name) だが username はメール形式（ユニット）
    """
    email: Optional[EmailStr] = None
    username: Optional[str] = None          # メール形式なら email として扱う
    display_name: Optional[str] = None
    password: str = Field(min_length=8)

class RegisterIn(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[str] = None
    password: str
    display_name: Optional[str] = None

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("password")
    @classmethod
    def check_password(cls, v):
        if not v:
            raise ValueError("password required")
        return v

    # email/username どちらも未指定ならエラー
    def model_post_init(self, _):
        if not (self.email or self.username):
            raise ValueError("email or username is required")

class RegisterResponse(BaseModel):
    user_id: int
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class LoginIn(BaseModel):
    email: Optional[str] = None
    username: Optional[str] = None
    password: str

    def model_post_init(self, _):
        if not (self.email or self.username):
            raise ValueError("email or username is required")
        if not self.password:
            raise ValueError("password is required")

class LoginRequest(BaseModel):
    """
    ログインはテスト側が email フィールドを送る想定。
    DB の User.email が無いので、User.username と突き合わせる。
    """
    email: EmailStr
    password: str


class TokenRefreshRequest(BaseModel):
    refresh_token: str


class AuthTokens(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AccessTokenOnly(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ------------------------------------------------------------
# ルーター
# (/api/v1 は main.py でまとめて付与されるため、ここは /auth のみ)
# ------------------------------------------------------------
router = APIRouter(prefix="/auth", tags=["auth"])

# EmailStr 検証アダプタ（Pydantic v2）
_EMAIL_ADAPTER = TypeAdapter(EmailStr)


# ------------------------------------------------------------
# ユーティリティ
# ------------------------------------------------------------
def _derive_email_and_username(payload: RegisterRequest) -> Tuple[str, Optional[str]]:
    """
    入力から (email_str, display_name_or_none) を確定する。
    - email が入っていればそれを採用
    - email が無く、username がメール形式ならそれを email として採用
    - それ以外は 422

    ここでの email は DB では User.username に保存する方針（email カラム非存在のため）。
    """
    if payload.email:
        return str(payload.email), (payload.display_name or None)

    if payload.username and "@" in payload.username:
        # username がメールっぽい → 検証して email として扱う
        try:
            validated: EmailStr = _EMAIL_ADAPTER.validate_python(payload.username)
        except ValidationError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid email format.",
            )
        return str(validated), (payload.display_name or None)

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Either 'email' must be provided or 'username' must be a valid email string.",
    )


# ------------------------------------------------------------
# エンドポイント
# ------------------------------------------------------------
@router.post("/register", response_model=AuthTokens, status_code=status.HTTP_201_CREATED)
def register_user(payload: RegisterRequest, db: Session = Depends(get_db)):
    """
    新規ユーザー登録。
    - DB に email カラムが無い環境もあるため、email は必ず User.username に保存。
    - email カラムが存在する環境では User.email にも保存（NOT NULL/UNIQUE に備える）。
    - display_name カラムが存在する場合のみ設定。
    """
    email_str, display_name = _derive_email_and_username(payload)

    # 既存チェックは username で（email を username に格納する実装）
    existing = (
        db.query(models.User)
        .filter(models.User.username == email_str)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already exists",
        )

    password_hash = hash_password(payload.password)

    user = models.User(
        username=email_str,
        password_hash=password_hash,
    )

    # ✅ email カラムがあるなら email も同じ値で埋める（e2e 環境の NOT NULL/UNIQUE に対応）
    if hasattr(models.User, "email"):
        setattr(user, "email", email_str)

    # 任意の display_name カラム対応
    if hasattr(models.User, "display_name") and display_name:
        setattr(user, "display_name", display_name)

    db.add(user)
    db.commit()
    db.refresh(user)

    # トークン即時発行（security.create_* は sub=... を渡す実装に統一）
    access_token = create_access_token(sub=str(user.id))
    refresh_token = create_refresh_token(sub=str(user.id))
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/login", response_model=AuthTokens)
def login_user(payload: LoginRequest, db: Session = Depends(get_db)):
    """
    username/email のどちらでもログイン可能にする。
    """
    id_key = (payload.username or payload.email or "").strip()
    if not id_key:
        raise HTTPException(status_code=400, detail="username or email required")

    q = db.query(models.User)
    user = None

    # username 優先で検索
    user = q.filter(models.User.username == id_key).first()

    # 見つからず email カラムがあるなら email でも検索
    if not user and hasattr(models.User, "email"):
        user = q.filter(models.User.email == id_key).first()

    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")

    access_token = create_access_token(sub=str(user.id))
    refresh_token = create_refresh_token(sub=str(user.id))
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/token/refresh", response_model=AuthTokens)
def refresh_access_token(payload: TokenRefreshRequest):
    try:
        decoded = decode_token(payload.refresh_token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    if decoded.get("type") != "refresh" or not decoded.get("sub"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    uid_str = str(decoded["sub"])  # ← 常に str で渡す
    # 毎回新規発行（前回の文字列を再利用しない！）
    new_access  = create_access_token(sub=uid_str)
    new_refresh = create_refresh_token(sub=uid_str)

    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
    }