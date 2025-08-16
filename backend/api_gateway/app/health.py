# backend/api_gateway/app/health.py
from fastapi import APIRouter
from sqlalchemy import text
from shared.app.database import SessionLocal

router = APIRouter(prefix="/api/v1/healthz", tags=["health"])

@router.get("")
def alive():
    return {"ok": True}

@router.get("/db")
def db_ok():
    db = SessionLocal()
    try:
        # SQLAlchemy 2.x 互換の text() 経由
        val = db.execute(text("SELECT 1")).scalar()
        return {"ok": bool(val == 1)}
    finally:
        db.close()
