# backend/api_gateway/app/security.py
# -*- coding: utf-8 -*-
"""
セキュリティ・トークン関連ユーティリティ
- パスワードハッシュ/検証
- JWT 発行/検証（jti/nonce を付与して同秒連打でもトークンが一意になるように）
- FastAPI Depends: 認証ユーザーの取得（必須 / 任意）
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Optional, Dict, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from shared.app.database import get_db
from shared.app import models

# =========================
# 環境変数・定数
# =========================
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret")
JWT_ALGO = os.getenv("JWT_ALGO", "HS256")
ACCESS_TTL_SEC = int(os.getenv("ACCESS_TOKEN_TTL_SEC", "3600"))       # 1h
REFRESH_TTL_SEC = int(os.getenv("REFRESH_TOKEN_TTL_SEC", "86400"))    # 24h

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 認証必須用: ヘッダ欠如で 401 を返す
auth_scheme = HTTPBearer(auto_error=True)
# 認証任意用: ヘッダ欠如でもエラーにしない
auth_scheme_optional = HTTPBearer(auto_error=False)

# =========================
# password
# =========================
def hash_password(raw: str) -> str:
    return pwd_context.hash(raw)

def verify_password(raw: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(raw, hashed)
    except Exception:
        return False

# =========================
# JWT
# =========================
def _create_token(*, sub: str, token_type: str, ttl: int) -> str:
    """
    jti / nonce を必ず含め、同一秒発行でも値が変わるようにする
    """
    now = int(time.time())
    payload = {
        "sub": sub,
        "type": token_type,
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
        "nonce": uuid.uuid4().hex[:12],
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)

def create_access_token(*, sub: str, token_type: str = "access") -> str:
    return _create_token(sub=sub, token_type=token_type, ttl=ACCESS_TTL_SEC)

def create_refresh_token(*, sub: str, token_type: str = "refresh") -> str:
    return _create_token(sub=sub, token_type=token_type, ttl=REFRESH_TTL_SEC)

def decode_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])

# =========================
# Depends: 認証ユーザー（必須）
# =========================
def get_current_user(
    cred: HTTPAuthorizationCredentials = Depends(auth_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    """
    Authorization ヘッダが必須。アクセストークンの検証を行い、ユーザーを返す。
    """
    if not cred or not cred.credentials:
        raise HTTPException(status_code=401, detail="not authenticated")

    try:
        decoded = decode_token(cred.credentials)
    except Exception:
        raise HTTPException(status_code=401, detail="invalid token")

    if decoded.get("type") != "access":
        raise HTTPException(status_code=401, detail="access token required")

    uid = decoded.get("sub")
    if not uid:
        raise HTTPException(status_code=401, detail="invalid token (no sub)")

    user = db.query(models.User).filter(models.User.id == int(uid)).first()
    if not user:
        raise HTTPException(status_code=401, detail="user not found")

    return user

# =========================
# Depends: 認証ユーザー（任意）
# =========================
def get_current_user_optional(
    cred: Optional[HTTPAuthorizationCredentials] = Depends(auth_scheme_optional),
    db: Session = Depends(get_db),
) -> Optional[models.User]:
    """
    Authorization ヘッダが無くても OK。あれば検証し、正しければユーザーを返す。
    - 未ログインの許容が必要なエンドポイントから利用（chat など）
    """
    # ヘッダなし → 未ログインとして None を返す
    if cred is None or not cred.credentials:
        return None

    # ヘッダあり → 通常の検証（不正なら 401）
    try:
        decoded = decode_token(cred.credentials)
    except Exception:
        raise HTTPException(status_code=401, detail="invalid token")

    if decoded.get("type") != "access":
        raise HTTPException(status_code=401, detail="access token required")

    uid = decoded.get("sub")
    if not uid:
        raise HTTPException(status_code=401, detail="invalid token (no sub)")

    user = db.query(models.User).filter(models.User.id == int(uid)).first()
    if not user:
        # トークンはあるが該当ユーザーがいない → 不正扱い
        raise HTTPException(status_code=401, detail="user not found")

    return user
