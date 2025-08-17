# -*- coding: utf-8 -*-
"""
認証・ユーザー管理エンドポイント
- POST /api/v1/auth/register
- POST /api/v1/auth/login        ← JSON と x-www-form-urlencoded の両方を受け付ける
- POST /api/v1/auth/token/refresh

要点:
- ルーターの prefix は "/auth"（/api/v1 は main.py 側で付与）
- DB モデルに email カラムが無い前提で、email は User.username に保存・検索する
- /register は
    1) {"email", "password"} 形式（E2E）
    2) {"username"(=メール文字列), "password", "display_name"} 形式（ユニット）
  の両対応（display_name は DB に保存しない）
- /login は JSON でも form（application/x-www-form-urlencoded）でも受け付ける
- Pydantic v2: EmailStr 検証は TypeAdapter を使用
"""

from __future__ import annotations

from typing import Optional, Tuple

from fastapi import Body, Form, APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, TypeAdapter, ValidationError, field_validator
from sqlalchemy.orm import Session

from shared.app.database import get_db
from shared.app import models
from api_gateway.app import security


# ------------------------------------------------------------
# ルーター
# (/api/v1 は main.py でまとめて付与されるため、ここは /auth のみ)
# ------------------------------------------------------------
router = APIRouter(prefix="/auth", tags=["auth"])

# EmailStr 検証アダプタ（Pydantic v2）
_EMAIL_ADAPTER = TypeAdapter(EmailStr)


# ------------------------------------------------------------
# 入出力スキーマ（shared を壊さないためにローカル定義）
# ------------------------------------------------------------

class RegisterRequest(BaseModel):
    # テストは email で送る（E2E）場合と username で送る（単体）場合があるので両対応
    email: Optional[EmailStr] = None
    username: Optional[str] = None  # username にメール文字列が入ってくる想定
    password: str
    display_name: Optional[str] = None  # DB に保存はしない（互換のため受けるだけ）

    @field_validator("password")
    @classmethod
    def _pw_len(cls, v: str) -> str:
        if not v:
            raise ValueError("password required")
        if len(v) < 8:
            raise ValueError("password too short")
        return v

class RegisterResponse(BaseModel):
    user_id: int

class LoginRequest(BaseModel):
    # JSON で来た時に利用（フォームには使わない）
    email: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None

class TokenRefreshRequest(BaseModel):
    refresh_token: str


# ------------------------------------------------------------
# ユーティリティ
# ------------------------------------------------------------
def _resolve_email_and_display(payload: RegisterRequest) -> Tuple[str, Optional[str]]:
    """
    入力から (email_str, display_name_or_none) を確定する。
    - email が入っていればそれを採用
    - email が無く、username がメール形式ならそれを email として採用
    - それ以外は 422
    """
    if payload.email:
        return str(payload.email), (payload.display_name or None)

    if payload.username:
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

@router.post("/register", status_code=201)
def register_user(payload: RegisterRequest, db: Session = Depends(get_db)):
    """
    ユーザーを作成する。DB の User モデルに email カラムは無い前提のため、
    email 文字列は username に保存する。
    """
    email, display_name = _resolve_email_and_display(payload)
    raw_password = payload.password

    # username（=メール文字列）での重複チェック
    existing = db.query(models.User).filter(models.User.username == email).first()
    if existing:
        # 409 を返しても E2E 側は許容（400/409 は「すでに存在」扱い）
        raise HTTPException(status_code=409, detail="user already exists")

    user = models.User(
        username=email,
        password_hash=security.hash_password(raw_password),
        # display_name は User のカラムに無い想定なので DB には保存しない
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # register では user_id のみ返す（テストはこれを期待）
    return {"user_id": user.id}


@router.post("/login")
def login_user(
    db: Session = Depends(get_db),
    payload: LoginIn | None = Body(None),
    username: str | None = Form(None),
    password: str | None = Form(None),
) -> dict:
    # JSON 優先、なければ form
    if payload is not None:
        login_id = payload.email or payload.username
        raw_pw = payload.password
    else:
        login_id = username
        raw_pw = password

    if not login_id or not raw_pw:
        raise HTTPException(status_code=422, detail="username/email and password are required")

    # email は username カラムに保存する方針
    user = db.query(models.User).filter(models.User.username == login_id).first()
    if not user or not security.verify_password(raw_pw, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")

    access = security.create_access_token(sub=str(user.id))
    refresh = security.create_refresh_token(sub=str(user.id))
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
    }

@router.post("/token/refresh")
def refresh_access_token(payload: TokenRefreshRequest) -> dict:
    """
    refresh_token から新しいアクセストークンと新しいリフレッシュトークンを発行する。
    """
    try:
        decoded = security.decode_token(payload.refresh_token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if decoded.get("type") != "refresh" or not decoded.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = str(decoded["sub"])
    # security 側の jti / nonce 実装により毎回異なるトークンが発行される想定
    new_access = security.create_access_token(sub=user_id)
    new_refresh = security.create_refresh_token(sub=user_id)

    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
    }
