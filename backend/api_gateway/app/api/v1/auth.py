# -*- coding: utf-8 -*-
"""
認証・ユーザー管理エンドポイント
- 変更点:
  - /register のリクエストボディで username をオプショナル化。
  - username 未指定の場合は email のローカル部から自動生成（重複時はサフィックス付与）。
  - 既存のトークン発行/検証ロジックやルート構成は変更しない。
"""

from __future__ import annotations

import re
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from api_gateway.app.security import (
    create_access_token,
    create_refresh_token,
    verify_password,
    get_password_hash,
    verify_refresh_token,
)
from shared.app.database import get_db
from shared.app import models

router = APIRouter(
    # 既存のルーター登録（prefix）は main.py 側の include_router に依存。
    # 本ファイル内では従来通りの設定にしているため、プレフィックスは変更しない。
    prefix="/api/v1/auth",
    tags=["auth"],
)

# -----------------------------
# 内部用スキーマ（username を Optional に）
# -----------------------------
USERNAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


class RegisterIn(BaseModel):
    """username を省略可能にした登録用モデル（E2E 想定: email/password のみでも登録可）"""
    email: EmailStr
    password: str = Field(min_length=8, description="8文字以上を推奨")
    username: Optional[str] = None


class RegisterOut(BaseModel):
    id: int
    email: EmailStr
    username: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshIn(BaseModel):
    refresh_token: str


# -----------------------------
# ユーティリティ
# -----------------------------
def _sanitize_username(name: str) -> str:
    """ユーザー名に使えない文字を除去し、先頭末尾のピリオド/ハイフン/アンダースコアも整形"""
    name = USERNAME_RE.sub("", name)
    name = name.strip("._-")
    # 空になった場合のフォールバック
    return name or f"user{secrets.token_hex(3)}"


def _derive_username_from_email(email: str) -> str:
    local = email.split("@", 1)[0]
    return _sanitize_username(local)


def _ensure_unique_username(db: Session, base_username: str) -> str:
    """既存重複があればランダムサフィックスを付与してユニーク化"""
    cand = base_username
    while True:
        exists = db.query(models.User).filter(models.User.username == cand).first()
        if not exists:
            return cand
        cand = f"{base_username}_{secrets.token_hex(2)}"


# -----------------------------
# エンドポイント
# -----------------------------
@router.post("/register", response_model=RegisterOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterIn, db: Session = Depends(get_db)):
    """
    ユーザー登録
    - username はオプショナル。未指定時は email ローカル部から自動生成（重複時はサフィックス）。
    - email 重複などの整合性違反は 409 を返す。
    """
    # username の補完
    if payload.username and payload.username.strip():
        base_username = _sanitize_username(payload.username)
    else:
        base_username = _derive_username_from_email(payload.email)

    username = _ensure_unique_username(db, base_username)
    hashed = get_password_hash(payload.password)

    user = models.User(email=payload.email, username=username, hashed_password=hashed)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # email のユニーク制約違反など
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="user already exists")
    db.refresh(user)

    return RegisterOut(id=user.id, email=user.email, username=user.username)


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    """
    ログイン
    - email/password を検証し、access/refresh を発行。
    """
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    access_token = create_access_token(subject=str(user.id))
    refresh_token = create_refresh_token(subject=str(user.id))
    return TokenOut(access_token=access_token, refresh_token=refresh_token)


@router.post("/token/refresh", response_model=TokenOut)
def refresh_token(payload: RefreshIn):
    """
    リフレッシュトークンを検証し、アクセストークンを再発行。
    - ここでは refresh もローテーション（新しい refresh を返却）する運用。
    """
    sub = verify_refresh_token(payload.refresh_token)  # 例外時は 401（security 側で発生）
    # sub にはユーザーID文字列が入る想定
    access_token = create_access_token(subject=sub)
    new_refresh_token = create_refresh_token(subject=sub)
    return TokenOut(access_token=access_token, refresh_token=new_refresh_token)
