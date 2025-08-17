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
@router.post("/register", response_model=RegisterResponse, status_code=201)
def register_user(payload: RegisterIn, db: Session = Depends(get_db)):
    """
    新規ユーザー登録。
    - 入力は email / username のどちらでも可
    - DB に email カラムが無いため、email は User.username に保存する（テスト実装準拠）
    - display_name は存在すれば User.display_name に入れる（存在しないスキーマでも壊さない）
    - レスポンスは user_id + トークン（bearer）
    """
    # 実際に username カラムへ格納する値を決定（email があればそれを使う）
    username_value = (payload.email or payload.username).strip()

    # 重複チェック（username カラムで一意）
    existing = db.query(models.User).filter(models.User.username == username_value).first()
    if existing:
        # e2e テストは 200/201/400/409 を許容 → 409 を返す
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")

    # パスワードハッシュ
    password_hash = hash_password(payload.password)

    # ユーザー作成
    user = models.User(username=username_value, password_hash=password_hash)
    if hasattr(models.User, "display_name") and payload.display_name:
        setattr(user, "display_name", payload.display_name)

    db.add(user)
    db.commit()
    db.refresh(user)

    # トークン
    access_token  = _issue_access_token_for(user.id)
    refresh_token = _issue_refresh_token_for(user.id)

    return {
        "user_id": user.id,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/login", response_model=AuthTokens)
def login_user(payload: LoginIn, db: Session = Depends(get_db)):
    """
    ログイン（JSON ボディ）
    - email / username のどちらでも受付
    - 既存の仕様に合わせて DB は User.username に email を格納しているため、そのまま一致検索
    """
    key = (payload.email or payload.username).strip()

    user = db.query(models.User).filter(models.User.username == key).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    return {
        "access_token": _issue_access_token_for(user.id),
        "refresh_token": _issue_refresh_token_for(user.id),
        "token_type": "bearer",
    }


@router.post("/token/refresh", response_model=AccessTokenOnly)
def refresh_access_token(payload: TokenRefreshRequest):
    """
    リフレッシュトークンからアクセストークンを再発行。
    - security.decode_token() で JWT を復号し、"type" == "refresh" と "sub" を検証。
    """
    try:
        decoded = decode_token(payload.refresh_token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    token_type = decoded.get("type")
    user_id = decoded.get("sub")
    if token_type != "refresh" or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    new_access = create_access_token(user_id=int(user_id))
    return AccessTokenOnly(access_token=new_access)
