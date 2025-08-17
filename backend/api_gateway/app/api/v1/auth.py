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
  の両対応（display_name は DB に無いので保存しない）
- /login は JSON でも form-encoded でも受け付ける（E2E は form を送る）
- Pydantic v2: EmailStr 検証は TypeAdapter を使用
- ※ 仕様は変えず、観測ログのみ追加（パスワードはマスク）
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status, Request, Form
from pydantic import BaseModel, EmailStr, TypeAdapter, ValidationError, field_validator
from sqlalchemy.orm import Session

from api_gateway.app import security
from shared.app.database import get_db
from shared.app import models

# ------------------------------------------------------------
# logger
# ------------------------------------------------------------
logger = logging.getLogger(__name__)
if not logger.handlers:
    # 親側で設定されていない環境向けに最低限の設定
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

# ------------------------------------------------------------
# 入出力スキーマ（shared を壊さないためにローカル定義）
# ------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: Optional[EmailStr] = None
    username: Optional[EmailStr] = None
    password: str
    display_name: Optional[str] = None  # 受け取るが保存しない

    @field_validator("password")
    @classmethod
    def _pw_len(cls, v: str) -> str:
        if len(v or "") < 8:
            raise ValueError("password too short")
        return v

    @property
    def login_id(self) -> str:
        if self.email:
            return str(self.email)
        if self.username:
            return str(self.username)
        raise ValueError("either email or username is required")


class TokenRefreshRequest(BaseModel):
    refresh_token: str


# ------------------------------------------------------------
# ルーター
# ------------------------------------------------------------
router = APIRouter(prefix="/auth", tags=["auth"])
_EMAIL_ADAPTER = TypeAdapter(EmailStr)


# ------------------------------------------------------------
# ユーティリティ
# ------------------------------------------------------------
def _extract_login_fields_from_any(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    raw_username = payload.get("username")
    raw_email = payload.get("email")
    password = payload.get("password")

    login_id = None
    if raw_email:
        login_id = str(raw_email).strip()
    elif raw_username:
        login_id = str(raw_username).strip()

    return login_id, (str(password) if password is not None else None)


def _normalize_email_like(value: str) -> str:
    try:
        validated: EmailStr = _EMAIL_ADAPTER.validate_python(value)
        return str(validated)
    except ValidationError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid email format.",
        )


def _mask_pw(pw: Optional[str]) -> str:
    return "***redacted***" if pw else ""


# ------------------------------------------------------------
# エンドポイント
# ------------------------------------------------------------
@router.post("/register", status_code=201)
def register_user(payload: RegisterRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    email_like = payload.login_id
    email_norm = _normalize_email_like(email_like)
    raw_password = payload.password

    logger.info("[/auth/register] received login_id(email-like)=%s", email_norm)

    existing = db.query(models.User).filter(models.User.username == email_norm).first()
    if existing:
        logger.info("[/auth/register] user already exists username=%s -> 409", email_norm)
        raise HTTPException(status_code=409, detail="user already exists")

    user = models.User(
        username=email_norm,
        password_hash=security.hash_password(raw_password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info("[/auth/register] success user_id=%s username=%s", user.id, user.username)
    return {
        "user_id": user.id,
        "username": user.username,
    }


@router.post("/login")
async def login_user(
    request: Request,
    # E2E（form）向けに Form を明示
    username: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    ctype = request.headers.get("content-type", "")
    logger.info("[/auth/login] content-type=%s", ctype)

    login_id: Optional[str] = None
    # まず Form を優先
    if email:
        login_id = email.strip()
    elif username:
        login_id = username.strip()

    # JSON フォールバック
    if not login_id or not password:
        try:
            data = await request.json()
        except Exception:
            data = {}
        if isinstance(data, dict):
            login_id2, password2 = _extract_login_fields_from_any(data)
            login_id = login_id or login_id2
            password = password or password2
        logger.info(
            "[/auth/login] parsed from json keys=%s",
            list(data.keys()) if isinstance(data, dict) else type(data).__name__,
        )

    logger.info(
        "[/auth/login] parsed login_id=%s password=%s (masked)",
        (login_id or ""), _mask_pw(password)
    )

    if not login_id or not password:
        logger.warning("[/auth/login] missing field(s): login_id=%s password_present=%s", bool(login_id), bool(password))
        raise HTTPException(status_code=422, detail="username/email and password are required")

    email_norm = _normalize_email_like(login_id)
    user = db.query(models.User).filter(models.User.username == email_norm).first()
    if not user or not security.verify_password(password, user.password_hash):
        logger.warning("[/auth/login] invalid credentials for username=%s", email_norm)
        raise HTTPException(status_code=401, detail="invalid credentials")

    access = security.create_access_token(sub=str(user.id))
    refresh = security.create_refresh_token(sub=str(user.id))

    logger.info("[/auth/login] success user_id=%s", user.id)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
    }


@router.post("/token/refresh")
def refresh_access_token(payload: TokenRefreshRequest) -> Dict[str, Any]:
    try:
        decoded = security.decode_token(payload.refresh_token)
    except Exception:
        logger.warning("[/auth/token/refresh] decode failed")
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if decoded.get("type") != "refresh" or not decoded.get("sub"):
        logger.warning("[/auth/token/refresh] invalid token payload=%s", decoded)
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = str(decoded["sub"])
    new_access = security.create_access_token(sub=user_id)
    new_refresh = security.create_refresh_token(sub=user_id)

    logger.info("[/auth/token/refresh] success user_id=%s", user_id)
    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
    }
