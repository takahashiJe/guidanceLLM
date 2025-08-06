# backend/api_gateway/app/api/v1/auth.py

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

# sharedディレクトリと、同じ階層のsecurity.pyからインポート
from backend.shared.app.database import get_db
from backend.shared.app import models, schemas
from backend.api_gateway.app import security

router = APIRouter()

@router.post("/register", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED)
def register_user(user_create: schemas.UserCreate, db: Session = Depends(get_db)):
    """
    FR-1-1: 新規ユーザー登録
    """
    db_user = db.query(models.User).filter(models.User.username == user_create.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    hashed_password = security.get_password_hash(user_create.password)
    db_user = models.User(username=user_create.username, hashed_password=hashed_password)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

@router.post("/login", response_model=schemas.Token)
def login_for_access_token(form_data: schemas.UserLogin, db: Session = Depends(get_db)):
    """
    FR-1-1: ログイン処理とトークン発行
    """
    user = db.query(models.User).filter(models.User.username == form_data.username).first()
    if not user or not security.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = security.create_access_token(data={"sub": user.username})
    refresh_token = security.create_refresh_token(data={"sub": user.username})
    
    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}

@router.post("/token/refresh", response_model=schemas.AccessToken)
def refresh_access_token(refresh_token: schemas.RefreshToken, db: Session = Depends(get_db)):
    """
    FR-1-1: リフレッシュトークンを使ったアクセストークンの再発行
    """
    try:
        payload = security.jwt.decode(refresh_token.refresh_token, security.SECRET_KEY, algorithms=[security.ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        
        user = db.query(models.User).filter(models.User.username == username).first()
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid refresh token")

        access_token = security.create_access_token(data={"sub": user.username})
        return {"access_token": access_token, "token_type": "bearer"}

    except security.JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
