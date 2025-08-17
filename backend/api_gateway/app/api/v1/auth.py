# -*- coding: utf-8 -*-
"""
認証エンドポイント
- /api/v1/auth/register
- /api/v1/auth/login
- /api/v1/auth/token/refresh

注意:
- ルーターには prefix を付けない（/api/v1/... は main.py 側で付与）
- models.User に display_name が無い環境に合わせ、登録時は username/password_hash のみ保存
"""

from __future__ import annotations

from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from shared.app.database import get_db
from shared.app import models
from api_gateway.app.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)

# ★ prefix は付けない（main.py で /api/v1/auth を付与）
router = APIRouter(tags=["auth"])


# -----------------------------
# リクエスト/レスポンス モデル
# -----------------------------
class RegisterRequest(BaseModel):
    username: str
    password: str
    # display_name は DB に無い環境があるので受付のみ（保存はしない）
    display_name: Optional[str] = None


class RegisterResponse(BaseModel):
    # テストは user_id か id のどちらでも受けるため両方返す
    user_id: str
    username: str
    id: Optional[str] = None


class LoginJSONRequest(BaseModel):
    username: str
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., description="現在のリフレッシュトークン")


# -----------------------------
# /register
# -----------------------------
@router.post("/register", response_model=RegisterResponse, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    # 既存チェック
    existing = db.query(models.User).filter(models.User.username == payload.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="username already exists")

    # ★ display_name は保存しない（User にカラムが無い環境のため）
    user = models.User(
        username=payload.username,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # 互換のため user_id と id を両方返す（どちらも文字列）
    return RegisterResponse(user_id=str(user.id), id=str(user.id), username=user.username)


# -----------------------------
# /login （JSON と x-www-form-urlencoded の両方を確実に受ける）
# -----------------------------
@router.post("/login", response_model=TokenPair)
async def login(request: Request, db: Session = Depends(get_db)):
    """
    Content-Type を見て自前で抽出することで、Form/JSON 併用時の 422 を回避する。
    """
    username, password = await _extract_login_credential(request)
    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="username/password is required (form or json).",
        )

    user = db.query(models.User).filter(models.User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")

    # アクセス/リフレッシュを毎回新規発行（security 側で jti/nonce 付与）
    access = create_access_token(sub=str(user.id), token_type="access")
    refresh = create_refresh_token(sub=str(user.id), token_type="refresh")
    return TokenPair(access_token=access, refresh_token=refresh)


async def _extract_login_credential(request: Request) -> Tuple[Optional[str], Optional[str]]:
    """
    - application/json: { "username": "...", "password": "..." }
    - application/x-www-form-urlencoded: username=...&password=...
    - それ以外でも、まず JSON → ダメなら form の順で試す（互換性重視）
    """
    ctype = (request.headers.get("content-type") or "").lower()

    # 1) JSON 優先
    if "application/json" in ctype:
        try:
            data = await request.json()
            # pydantic で型検証
            body = LoginJSONRequest.model_validate(data)
            return body.username, body.password
        except (ValidationError, Exception):
            # JSON と言っているが body 不正→後段の form 試行にフォールバック
            pass

    # 2) form
    if "application/x-www-form-urlencoded" in ctype or "multipart/form-data" in ctype or True:
        # True を含めておくことで、ctype 不明でも form() 試行して互換性を上げる
        try:
            form = await request.form()
            u = form.get("username")
            p = form.get("password")
            if u and p:
                return str(u), str(p)
        except Exception:
            pass

    # どちらも取れなかった
    return None, None


# -----------------------------
# /token/refresh
# -----------------------------
@router.post("/token/refresh", response_model=TokenPair)
def refresh_token(payload: RefreshRequest, db: Session = Depends(get_db)):
    try:
        decoded = decode_token(payload.refresh_token)
    except Exception:
        raise HTTPException(status_code=401, detail="invalid refresh token")

    if decoded.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="token type must be refresh")

    user_id = decoded.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="invalid refresh token (no sub)")

    # ユーザー存在確認
    user = db.query(models.User).filter(models.User.id == int(user_id)).first()
    if not user:
        raise HTTPException(status_code=401, detail="user not found")

    # 新規に発行（jti/nonce により同秒でも値が変わる）
    access = create_access_token(sub=str(user.id), token_type="access")
    refresh = create_refresh_token(sub=str(user.id), token_type="refresh")
    return TokenPair(access_token=access, refresh_token=refresh)
