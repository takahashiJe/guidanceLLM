# backend/api_gateway/app/api/v1/auth.py

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from shared.app.database import get_db
from shared.app import models, schemas
from api_gateway.app import security

router = APIRouter()

@router.post("/register", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
def register_user(user_create: schemas.UserCreate, db: Session = Depends(get_db)):
    """FR-1-1: 新規ユーザー登録"""
    db_user = db.query(models.User).filter(models.User.username == user_create.username).first()
    if db_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already registered")
    
    try:
        hashed_password = security.get_password_hash(user_create.password)
        db_user = models.User(username=user_create.username, hashed_password=hashed_password)
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return db_user
    except SQLAlchemyError as e:
        db.rollback()
        # main.pyのグローバルハンドラに捕捉させるため、再度raiseする
        raise e
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"An unexpected error occurred: {e}")


@router.post("/login", response_model=schemas.Token)
def login_for_access_token(db: Session = Depends(get_db), form_data: OAuth2PasswordRequestForm = Depends()):
    """FR-1-1: ログイン処理とトークン発行"""
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user or not security.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = security.create_access_token(data={"sub": user.username})
    refresh_token = security.create_refresh_token(data={"sub": user.username})
    
    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}

# /token/refresh エンドポイントは現在の実装で十分堅牢なため、変更なし
@router.post("/token/refresh", response_model=schemas.AccessToken)
def refresh_access_token(refresh_token: schemas.RefreshToken, db: Session = Depends(get_db)):
    """FR-1-1: リフレッシュトークンを使ったアクセストークンの再発行"""
    try:
        payload = security.jwt.decode(refresh_token.refresh_token, security.SECRET_KEY, algorithms=[security.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
        
        user = db.query(models.User).filter(models.User.username == username).first()
        if user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found for token")

        access_token = security.create_access_token(data={"sub": user.username})
        return {"access_token": access_token, "token_type": "bearer"}

    except security.JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")
