# backend/api_gateway/app/security.py
import os
import time
from typing import Optional, Tuple

from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from shared.app.database import get_db
from shared.app.models import User

load_dotenv()

# ==== 設定値（.env から取得）====
JWT_SECRET = os.getenv("JWT_SECRET", "CHANGE_ME_SECRET")
JWT_ALG = os.getenv("JWT_ALG", "HS256")

ACCESS_TOKEN_EXPIRE_SECONDS = int(os.getenv("ACCESS_TOKEN_EXPIRE_SECONDS", "3600"))       # 1h
REFRESH_TOKEN_EXPIRE_SECONDS = int(os.getenv("REFRESH_TOKEN_EXPIRE_SECONDS", "7776000"))  # 90d

# OAuth2PasswordBearerは「Authorization: Bearer <access_token>」を想定
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# パスワードハッシュ
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ===== パスワードユーティリティ =====
def hash_password(raw_password: str) -> str:
    return pwd_context.hash(raw_password)

def verify_password(raw_password: str, hashed: str) -> bool:
    return pwd_context.verify(raw_password, hashed)


# ===== JWT ユーティリティ =====
def _create_token(sub: str, token_type: str, expires_in: int) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "type": token_type,  # "access" or "refresh"
        "iat": now,
        "exp": now + expires_in,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def create_access_token(user_id: str) -> str:
    return _create_token(sub=user_id, token_type="access", expires_in=ACCESS_TOKEN_EXPIRE_SECONDS)

def create_refresh_token(user_id: str) -> str:
    return _create_token(sub=user_id, token_type="refresh", expires_in=REFRESH_TOKEN_EXPIRE_SECONDS)

def decode_token(token: str) -> dict:
    # 将来の余裕を見て leeway を設けたい場合は options で調整可
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    アクセストークンを検証し、現在ユーザーを返す依存関数。
    - type=access のみ許可
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            raise credentials_exc
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exc
        # sub は文字列で保持しているため DB の int に明示変換
        user_id_int = int(user_id)
    except (JWTError, ValueError):
        # ValueError は int 変換失敗時
        raise credentials_exc

    user: Optional[User] = db.query(User).filter(User.id == user_id_int).first()
    if user is None:
        raise credentials_exc
    return user

oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

def get_current_user_optional(
    token: Optional[str] = Depends(oauth2_scheme_optional),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """
    アクセストークンが無い/不正でも例外にせず None を返す依存関数。
    認証任意エンドポイントで使用。
    """
    if not token:
        return None

    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            return None
        user_id = payload.get("sub")
        if user_id is None:
            return None
        user_id_int = int(user_id)
    except (JWTError, ValueError):
        return None

    return db.query(User).filter(User.id == user_id_int).first()


def rotate_access_token_from_refresh(
    refresh_token: str,
    db: Session,
) -> Tuple[str, str]:
    """
    リフレッシュトークンを検証し、新しいアクセストークンを発行する。
    - リフレッシュも再発行（ローリング）する方針。必要に応じて変更可。
    """
    try:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise JWTError("invalid token type")
        user_id = payload.get("sub")
        if user_id is None:
            raise JWTError("no subject")
        user_id_int = int(user_id)
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # ユーザー存在確認（失効ユーザー等のガード）
    user = db.query(User).filter(User.id == user_id_int).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    new_access = create_access_token(user_id=str(user_id_int))
    new_refresh = create_refresh_token(user_id=str(user_id_int))
    return new_access, new_refresh
