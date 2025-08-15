# backend/api_gateway/app/api/v1/auth.py
# ユーザー登録 / ログイン / リフレッシュ
# - 401 リトライはクライアント実装（401検知→/token/refresh→元API再試行）により達成

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api_gateway.app.security import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    rotate_access_token_from_refresh,
)
from shared.app.database import get_db
from shared.app.models import User
from shared.app.schemas import (
    RegisterRequest,
    RegisterResponse,
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    RefreshResponse,
)

router = APIRouter()


@router.post("/register", response_model=RegisterResponse)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    # 既存ユーザー確認（username を一意制約想定）
    exists = db.query(User).filter(User.username == payload.username).first()
    if exists:
        raise HTTPException(status_code=400, detail="Username already exists")

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return RegisterResponse(user_id=str(user.id), username=user.username)


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    access = create_access_token(user_id=str(user.id))
    refresh = create_refresh_token(user_id=str(user.id))
    return LoginResponse(access_token=access, refresh_token=refresh, token_type="bearer")


@router.post("/token/refresh", response_model=RefreshResponse)
def refresh_token(payload: RefreshRequest, db: Session = Depends(get_db)):
    new_access, new_refresh = rotate_access_token_from_refresh(payload.refresh_token, db)
    return RefreshResponse(access_token=new_access, refresh_token=new_refresh, token_type="bearer")
