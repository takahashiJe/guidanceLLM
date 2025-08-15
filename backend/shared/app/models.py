# backend/shared/app/models.py
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, Text, Float, Enum, JSON, Boolean
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

# --- Users ---
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(128), unique=True, nullable=False, index=True)
    email = Column(String(256), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    preferred_language = Column(String(8), default="ja")
    refresh_token_jti = Column(String(64), nullable=True)  # refresh ローテ用

    sessions = relationship("Session", back_populates="user", cascade="all,delete")

# --- Sessions ---
class Session(Base):
    __tablename__ = "sessions"
    id = Column(String(64), primary_key=True)  # FE 生成（UUID 推奨）
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    current_status = Column(String(32), default="Browse")  # Browse / planning / navigating
    active_plan_id = Column(Integer, ForeignKey("plans.id"), nullable=True)
    language = Column(String(8), default="ja")
    dialogue_mode = Column(String(16), default="text")  # text / voice
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="sessions")
    messages = relationship("ConversationMessage", back_populates="session", cascade="all,delete")

# --- Conversation History ---
class ConversationMessage(Base):
    __tablename__ = "conversation_history"
    id = Column(Integer, primary_key=True)
    session_id = Column(String(64), ForeignKey("sessions.id"), index=True, nullable=False)
    role = Column(String(32), nullable=False)  # user / assistant / system_trigger
    content = Column(Text, nullable=False)
    meta = Column(JSON, nullable=True)  # 例: {"trigger": "PROXIMITY_GUIDE", "spot_id":"spot_xxx"}
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("Session", back_populates="messages")

# --- Plans / Stops（混雑集計に使用） ---
class Plan(Base):
    __tablename__ = "plans"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    session_id = Column(String(64), ForeignKey("sessions.id"), nullable=False)
    start_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    stops = relationship("Stop", back_populates="plan", cascade="all,delete")

class Stop(Base):
    __tablename__ = "stops"
    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("plans.id"), index=True, nullable=False)
    spot_id = Column(Integer, ForeignKey("spots.id"), index=True, nullable=False)
    order_index = Column(Integer, nullable=False)

    plan = relationship("Plan", back_populates="stops")
    spot = relationship("Spot")

# --- Spots / AccessPoints（情報提供・ルーティングで使用） ---
class Spot(Base):
    __tablename__ = "spots"
    id = Column(Integer, primary_key=True)
    official_name = Column(String(256), index=True)
    spot_type = Column(String(32), index=True)  # tourist_spot / accommodation / ...
    description = Column(Text, nullable=True)
    social_proof = Column(Text, nullable=True)
    tags = Column(Text, nullable=True)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)

class AccessPoint(Base):
    __tablename__ = "access_points"
    id = Column(Integer, primary_key=True)
    name = Column(String(256))
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    kind = Column(String(32), default="parking")  # parking / trailhead 等
    note = Column(Text, nullable=True)

# --- Pre-generated guide texts（ナビ開始時の事前生成） ---
class PreGeneratedGuide(Base):
    __tablename__ = "pre_generated_guides"
    id = Column(Integer, primary_key=True)
    session_id = Column(String(64), ForeignKey("sessions.id"), index=True, nullable=False)
    spot_id = Column(Integer, ForeignKey("spots.id"), nullable=False)
    lang = Column(String(8), default="ja")
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
