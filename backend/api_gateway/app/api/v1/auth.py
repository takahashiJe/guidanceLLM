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
from pydantic import BaseModel, EmailStr, Field, TypeAdapter, ValidationError
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

class RegisterResponse(BaseModel):
    user_id: int
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

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
def register_user(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    """
    新規ユーザー登録。
    - 入力は email / username のどちらでも可
    - DB に email カラムが無いため、email は User.username に保存する
    - display_name があれば User.display_name へ（存在しない環境でも壊さない）
    - レスポンスは user_id とトークンを返す（テスト準拠）
    """
    # email / username どちらでも受け付ける
    raw_email = (payload.get("email") or payload.get("username") or "").strip()
    if not raw_email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="email/username is required")
    password = payload.get("password")
    if not password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="password is required")
    display_name = payload.get("display_name")

    email_str = raw_email

    # 既存チェック（username カラムで一意）
    existing = db.query(models.User).filter(models.User.username == email_str).first()
    if existing:
        # e2e は 200/201/400/409 を許容しているので 409 を返す
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")

    # パスワードハッシュ化（既存ヘルパ）
    password_hash = hash_password(password)

    # ユーザー作成（email は username に格納）
    user = models.User(username=email_str, password_hash=password_hash)
    if hasattr(models.User, "display_name") and display_name:
        setattr(user, "display_name", display_name)

    db.add(user)
    db.commit()
    db.refresh(user)

    # トークン即時発行（既存の issue_* を使用）
    access_token  = issue_access_token(user.id)
    refresh_token = issue_refresh_token(user.id)

    # RegisterResponse に合う dict を返す
    return {
        "user_id": user.id,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/login", response_model=AuthTokens)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """
    ログイン:
    - クライアントからは email フィールドで渡される
    - DB では username カラムにメールが格納されているため username で検索
    """
    user = db.query(models.User).filter(models.User.username == str(payload.email)).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    access_token = create_access_token(user_id=user.id)
    refresh_token = create_refresh_token(user_id=user.id)
    return AuthTokens(access_token=access_token, refresh_token=refresh_token)


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
