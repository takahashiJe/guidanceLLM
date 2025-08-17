# backend/api_gateway/app/api/v1/auth.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session

from api_gateway.app.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from shared.app.database import get_db
from shared.app import models

router = APIRouter(prefix="/auth", tags=["auth"])

# ---------- Schemas ----------
class RegisterRequest(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[EmailStr] = None  # ユニットテストは username をメールとして送る
    password: str
    display_name: Optional[str] = None

    @field_validator("password")
    @classmethod
    def _pw_len(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("password too short")
        return v

    @property
    def login_id(self) -> str:
        if self.email:
            return str(self.email)
        if self.username:
            return str(self.username)
        raise ValueError("either email or username is required")

class LoginRequest(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[EmailStr] = None
    password: str

    @property
    def login_id(self) -> str:
        if self.email:
            return str(self.email)
        if self.username:
            return str(self.username)
        raise ValueError("either email or username is required")

class TokenRefreshRequest(BaseModel):
    refresh_token: str


# ---------- Endpoints ----------
@router.post("/register", status_code=201)
def register_user(payload: RegisterRequest, db: Session = Depends(get_db)):
    email = payload.login_id
    display_name = payload.display_name or email.split("@")[0]
    raw_password = payload.password

    existing = db.query(models.User).filter(models.User.email == email).first()
    if existing:
        # E2E 側は 400/409 を「既存扱い」として許容
        raise HTTPException(status_code=409, detail="user already exists")

    user = models.User(
        email=email,
        display_name=display_name,
        password_hash=hash_password(raw_password),   # ← 直接呼び出し
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # 単体テストは register の戻りに user_id を期待
    return {
        "user_id": user.id,
        "email": user.email,
        "display_name": user.display_name,
    }

@router.post("/login")
def login_user(payload: LoginRequest, db: Session = Depends(get_db)) -> dict:
    login_id = payload.login_id
    user = db.query(models.User).filter(models.User.email == login_id).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")

    access = create_access_token(sub=str(user.id))     # ← 直接呼び出し
    refresh = create_refresh_token(sub=str(user.id))   # ← 直接呼び出し
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
    }

@router.post("/token/refresh")
def refresh_access_token(payload: TokenRefreshRequest) -> dict:
    try:
        decoded = decode_token(payload.refresh_token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if decoded.get("type") != "refresh" or not decoded.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = str(decoded["sub"])
    # security.py は jti/nonce 付きなので毎回異なるトークンになる
    new_access = create_access_token(sub=user_id)
    new_refresh = create_refresh_token(sub=user_id)

    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
    }
