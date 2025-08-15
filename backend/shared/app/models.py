# -*- coding: utf-8 -*-
"""
SQLAlchemy モデル定義。
既存の User/Session/Plan/Stop/Spot/ConversationHistory 等は現行を尊重。
本差分では pre_generated_guides を追加する。
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, DateTime, Float, ForeignKey, Text
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()

# --- 既存想定モデル（必要最小限の定義例。既存定義がある場合はそちらを優先し、重複定義を削る） ---

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(128), unique=True, index=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Session(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True)
    session_id = Column(String(64), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    current_status = Column(String(32), default="Browse")  # Browse / planning / navigating
    active_plan_id = Column(Integer, ForeignKey("plans.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Plan(Base):
    __tablename__ = "plans"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    start_date = Column(String(10), nullable=True)  # "YYYY-MM-DD"
    created_at = Column(DateTime, default=datetime.utcnow)


class Stop(Base):
    __tablename__ = "stops"
    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=False)
    spot_id = Column(Integer, ForeignKey("spots.id"), nullable=False)
    order_index = Column(Integer, nullable=False, default=0)


class Spot(Base):
    __tablename__ = "spots"
    id = Column(Integer, primary_key=True)
    spot_type = Column(String(32), nullable=False, default="tourist_spot")  # tourist_spot / accommodation / etc.
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    official_name_ja = Column(String(256), nullable=True)
    official_name_en = Column(String(256), nullable=True)
    official_name_zh = Column(String(256), nullable=True)
    description_ja = Column(Text, nullable=True)
    description_en = Column(Text, nullable=True)
    description_zh = Column(Text, nullable=True)
    social_proof_ja = Column(Text, nullable=True)
    social_proof_en = Column(Text, nullable=True)
    social_proof_zh = Column(Text, nullable=True)
    tags = Column(Text, nullable=True)       # JSON/TEXT でタグ配列相当を格納
    category = Column(Text, nullable=True)   # 補助カテゴリ（検索用）
    popularity = Column(Integer, nullable=True)


class ConversationHistory(Base):
    __tablename__ = "conversation_history"
    id = Column(Integer, primary_key=True)
    session_id = Column(String(64), index=True, nullable=False)
    role = Column(String(16), nullable=False)   # "user" / "assistant" / "system"
    content = Column(Text, nullable=False)      # SYSTEM_TRIGGER を含む
    created_at = Column(DateTime, default=datetime.utcnow)


# --- 新規: 事前生成ガイドの保管（FR-5-4-1） ---

class PreGeneratedGuide(Base):
    __tablename__ = "pre_generated_guides"
    id = Column(Integer, primary_key=True)
    session_id = Column(String(64), index=True, nullable=False)  # Session.session_id に対応
    spot_id = Column(Integer, ForeignKey("spots.id"), nullable=False)
    lang = Column(String(8), nullable=False, default="ja")       # ja/en/zh
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # 参照関係（必要なら）
    # spot = relationship("Spot")
