# /backend/worker/app/db/models.py

import datetime
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    DateTime,
    Float,
    ForeignKey,
    JSON,
    Text,
)
from sqlalchemy.orm import relationship, declarative_base

# 全てのモデルクラスが継承する基本クラス
Base = declarative_base()

class User(Base):
    """ユーザー情報を格納するテーブル"""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, index=True, nullable=False)
    selected_language = Column(String, default="ja")
    profile = Column(JSON) # スキルレベルなどのプロフィール情報
    
    # リレーションシップ
    conversations = relationship("Conversation", back_populates="user")
    visit_plans = relationship("VisitPlan", back_populates="user")
    location_history = relationship("LocationHistory", back_populates="user")

class Conversation(Base):
    """会話履歴を格納するテーブル"""
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.user_id"), nullable=False, index=True)
    message_type = Column(String, nullable=False) # "human" or "ai"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    # リレーションシップ
    user = relationship("User", back_populates="conversations")

class VisitPlan(Base):
    """訪問計画を格納するテーブル"""
    __tablename__ = "visit_plans"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.user_id"), nullable=False, index=True)
    spot_id = Column(String, nullable=False, index=True)
    spot_name = Column(String, nullable=False, index=True)
    visit_date = Column(DateTime, nullable=False, index=True)
    
    # リレーションシップ
    user = relationship("User", back_populates="visit_plans")

class LocationHistory(Base):
    """案内中の位置情報履歴を格納するテーブル"""
    __tablename__ = "location_history"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, ForeignKey("users.user_id"), nullable=False, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # リレーションシップ
    user = relationship("User", back_populates="location_history")

class ActiveRoute(Base):
    """現在案内中のルート情報を格納するテーブル"""
    __tablename__ = "active_routes"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, index=True, nullable=False)
    route_data = Column(JSON, nullable=False) # GeoJSON形式のルートデータ