# backend/api_gateway/app/api/v1/auth.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
import uuid

from shared.app.database import get_db
from shared.app import models, schemas
from api_gateway.app.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
)

router = APIRouter()

@router.post("/register", response_model=schemas.TokenPair)
def register(payload: schemas.UserCreate, db: Session = Depends(get_db)):
    exists = db.query(models.User).filter(
        (models.User.username == payload.username) | (models.User.email == payload.email)
    ).first()
    if exists:
        raise HTTPException(status_code=400, detail="User already exists")
    user = models.User(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        preferred_language=payload.preferred_language or "ja",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    # refresh token の jti をユーザーに保存（ローテーション前提）
    jti = str(uuid.uuid4())
    user.refresh_token_jti = jti
    db.commit()
    access = create_access_token(sub=user.username)
    refresh = create_refresh_token(sub=user.username, jti=jti)
    return schemas.TokenPair(access_token=access, refresh_token=refresh)

@router.post("/login", response_model=schemas.TokenPair)
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == payload.username).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    # ログイン時も refresh をローテーション
    import uuid
    jti = str(uuid.uuid4())
    user.refresh_token_jti = jti
    db.commit()
    access = create_access_token(sub=user.username)
    refresh = create_refresh_token(sub=user.username, jti=jti)
    return schemas.TokenPair(access_token=access, refresh_token=refresh)

@router.post("/token/refresh", response_model=schemas.AccessToken)
def refresh_token(payload: schemas.TokenRefreshRequest, db: Session = Depends(get_db)):
    from jose import JWTError
    from api_gateway.app.security import decode_token
    try:
        decoded = decode_token(payload.refresh_token)
        if decoded.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        username = decoded.get("sub")
        jti = decoded.get("jti")
        if not username or not jti:
            raise HTTPException(status_code=401, detail="Invalid refresh token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    user = db.query(models.User).filter(models.User.username == username).first()
    if not user or user.refresh_token_jti != jti:
        raise HTTPException(status_code=401, detail="Refresh token is not recognized (rotated)")
    # Refresh ローテーション：新しい jti を付与
    new_jti = str(uuid.uuid4())
    user.refresh_token_jti = new_jti
    db.commit()
    from api_gateway.app.security import create_access_token, create_refresh_token
    new_access = create_access_token(sub=username)
    new_refresh = create_refresh_token(sub=username, jti=new_jti)
    return schemas.AccessToken(access_token=new_access, refresh_token=new_refresh)
