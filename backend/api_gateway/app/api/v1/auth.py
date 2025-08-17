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
  の両対応（display_name は DB には保存しない）
- /login は JSON でも form-encoded でも受け付ける（E2E は form を送る）
- Pydantic v2: EmailStr 検証は TypeAdapter を使用
"""

from __future__ import annotations

from datetime import timedelta, datetime
from typing import Optional, Tuple, Dict, Any
import json
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, EmailStr, TypeAdapter, ValidationError, field_validator
from sqlalchemy.orm import Session

from api_gateway.app import security
from shared.app.database import get_db
from shared.app import models


# ------------------------------------------------------------
# 入出力スキーマ（shared を壊さないためにローカル定義）
# ------------------------------------------------------------
class RegisterRequest(BaseModel):
    # テストは email で送る（E2E）場合と username で送る（単体）場合があるので両対応
    email: Optional[EmailStr] = None
    username: Optional[EmailStr] = None
    password: str
    # display_name は入力は許容するが、DB には保存しない（モデルに無いため）
    display_name: Optional[str] = None

    @field_validator("password")
    @classmethod
    def _pw_len(cls, v: str) -> str:
        if len(v or "") < 8:
            raise ValueError("password too short")
        return v

    @property
    def login_id(self) -> str:
        # email 優先、なければ username
        if self.email:
            return str(self.email)
        if self.username:
            return str(self.username)
        raise ValueError("either email or username is required")


class TokenRefreshRequest(BaseModel):
    refresh_token: str


# レスポンス系（テストで参照されるので残す）
class AuthTokens(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AccessTokenOnly(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ------------------------------------------------------------
# ルーター
# ------------------------------------------------------------
router = APIRouter(prefix="/auth", tags=["auth"])

# EmailStr 検証アダプタ（Pydantic v2）
_EMAIL_ADAPTER = TypeAdapter(EmailStr)


# ------------------------------------------------------------
# ユーティリティ
# ------------------------------------------------------------
def _extract_login_fields_from_mapping(payload: Any) -> Tuple[Optional[str], Optional[str]]:
    """
    dict 互換オブジェクトや Starlette の FormData などから (login_id, password) を抽出。
    email / username のいずれかを login_id として扱う。
    """
    try:
        get = payload.get  # type: ignore[attr-defined]
    except Exception:
        return None, None

    raw_username = get("username")
    raw_email = get("email")
    password = get("password")
    login_id = None

    if raw_email:
        login_id = str(raw_email).strip()
    elif raw_username:
        login_id = str(raw_username).strip()

    return login_id, (str(password) if password is not None else None)


def _normalize_email_like(value: str) -> str:
    """メールっぽい文字列を EmailStr で検証し、正規化して返す。失敗時は HTTP 422。"""
    try:
        validated: EmailStr = _EMAIL_ADAPTER.validate_python(value)
        return str(validated)
    except ValidationError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid email format.",
        )


# ------------------------------------------------------------
# エンドポイント
# ------------------------------------------------------------
@router.post("/register", status_code=201)
def register_user(payload: RegisterRequest, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    - email もしくは username（どちらもメール文字列）＋ password を受け取る
    - User.username にメールを保存
    - display_name はモデルに無いので保存しない
    - レスポンスは user_id を返す（E2E/ユニットの期待どちらにも合う）
    """
    email_like = payload.login_id
    email_norm = _normalize_email_like(email_like)
    raw_password = payload.password

    # 重複チェック
    existing = db.query(models.User).filter(models.User.username == email_norm).first()
    if existing:
        # 409 を返しても E2E 側は許容（400/409 は「すでに存在」扱い）
        raise HTTPException(status_code=409, detail="user already exists")

    user = models.User(
        username=email_norm,
        password_hash=security.hash_password(raw_password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # register ではトークンを返さない（ユニット側も user_id を参照）
    return {
        "user_id": user.id,
        "username": user.username,
    }


@router.post("/login")
async def login_user(request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """
    - JSON でも form-encoded でも受け付ける
    - body が JSON の場合: {"username" or "email", "password"}
    - body が form の場合: username=...&password=...（E2E 想定）
    - 取り出しに失敗した場合は raw body の parse / QueryString までフォールバック
    """
    login_id: Optional[str] = None
    password: Optional[str] = None

    # 1) Content-Type 判定は小文字化して部分一致
    ctype = (request.headers.get("content-type") or "").lower()

    # 2) フォーム（x-www-form-urlencoded / multipart）
    if ("application/x-www-form-urlencoded" in ctype) or ("multipart/form-data" in ctype):
        form = await request.form()
        # まずは FormData として
        login_id, password = _extract_login_fields_from_mapping(form)
        # だめなら dict(form) 経由で再取得
        if not login_id or not password:
            login_id, password = _extract_login_fields_from_mapping(dict(form))
        # さらにダメなら raw body を parse_qs
        if not login_id or not password:
            body_bytes = await request.body()
            try:
                parsed = {k: v[0] for k, v in parse_qs(body_bytes.decode(errors="ignore")).items() if v}
            except Exception:
                parsed = {}
            login_id, password = _extract_login_fields_from_mapping(parsed)

    # 3) JSON（application/json やその他でも json() が通る場合がある）
    else:
        data: Any = {}
        try:
            data = await request.json()
        except Exception:
            # raw body を JSON として再解釈
            body_bytes = await request.body()
            try:
                data = json.loads(body_bytes.decode(errors="ignore"))
            except Exception:
                data = {}

        if isinstance(data, dict):
            login_id, password = _extract_login_fields_from_mapping(data)

    # 4) 最後のフォールバック：クエリ文字列も見る
    if not login_id or not password:
        qs = parse_qs(str(request.url.query))
        parsed_qs = {k: v[0] for k, v in qs.items() if v}
        q_login, q_pass = _extract_login_fields_from_mapping(parsed_qs)
        login_id = login_id or q_login
        password = password or q_pass

    if not login_id or not password:
        raise HTTPException(status_code=422, detail="username/email and password are required")

    # username 側にメール文字列を保存している前提
    email_norm = _normalize_email_like(login_id)
    user = db.query(models.User).filter(models.User.username == email_norm).first()
    if not user or not security.verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")

    # 既存の security ユーティリティを使用（サブジェクトは user.id）
    access = security.create_access_token(sub=str(user.id))
    refresh = security.create_refresh_token(sub=str(user.id))

    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
    }


@router.post("/token/refresh")
def refresh_access_token(payload: TokenRefreshRequest) -> Dict[str, Any]:
    """
    - refresh_token を security.decode_token で検証
    - type が refresh かつ sub が存在することを確認
    - 新しい access / refresh を払い出す
    """
    try:
        decoded = security.decode_token(payload.refresh_token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if decoded.get("type") != "refresh" or not decoded.get("sub"):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = str(decoded["sub"])
    new_access = security.create_access_token(sub=user_id)
    new_refresh = security.create_refresh_token(sub=user_id)  # テストが refresh も期待

    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
    }
