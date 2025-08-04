# shared/app/models.py

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Double,
    ARRAY,
    UUID, # UUID型を追加
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func
from geoalchemy2 import Geometry # PostGIS用の型

Base = declarative_base()

class User(Base):
    """
    FR-1-1: ユーザーアカウント情報を管理
    """
    __tablename__ = "users"
    user_id = Column(Integer, primary_key=True)
    username = Column(String(255), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    sessions = relationship("Session", back_populates="user")
    plans = relationship("Plan", back_populates="user")

class Session(Base):
    """
    FR-1-2, FR-1-3: 会話セッションの状態を管理
    """
    __tablename__ = "sessions"
    session_id = Column(UUID(as_uuid=True), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    app_status = Column(String(50), nullable=False, default='Browse')
    active_plan_id = Column(Integer, ForeignKey("plans.plan_id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="sessions")
    plan = relationship("Plan")
    history = relationship("ConversationHistory", back_populates="session")

class ConversationHistory(Base):
    """
    FR-2-2, FR-2-3: 全ての対話ターンを記録
    """
    __tablename__ = "conversation_history"
    message_id = Column(Integer, primary_key=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.session_id"), nullable=False)
    turn = Column(Integer, nullable=False)
    user_input = Column(Text) # システムトリガーもここに記録
    ai_output = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("Session", back_populates="history")

class Spot(Base):
    """
    FR-3: POIマスターデータ
    """
    __tablename__ = "spots"
    spot_id = Column(Text, primary_key=True)
    official_name_ja = Column(Text)
    official_name_en = Column(Text)
    official_name_zh = Column(Text)
    description_ja = Column(Text)
    description_en = Column(Text)
    description_zh = Column(Text)
    tags_ja = Column(ARRAY(Text))
    tags_en = Column(ARRAY(Text))
    tags_zh = Column(ARRAY(Text))
    social_proof_ja = Column(Text)
    social_proof_en = Column(Text)
    social_proof_zh = Column(Text)
    latitude = Column(Double)
    longitude = Column(Double)
    geom = Column(Geometry(geometry_type='POINT', srid=4326), nullable=False)
    source_urls = Column(ARRAY(Text))
    opening_hours = Column(Text)
    admission_fee = Column(Text)
    website_url = Column(Text)

class Plan(Base):
    """
    FR-4-1: 周遊計画のマスター
    """
    __tablename__ = "plans"
    plan_id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    plan_name = Column(String(255))
    start_date = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="plans")
    stops = relationship("Stop", back_populates="plan", order_by="Stop.stop_order")

class Stop(Base):
    """
    FR-4-1, FR-4-2: 周遊計画に含まれる訪問先の順序リスト
    """
    __tablename__ = "stops"
    stop_id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("plans.plan_id"), nullable=False)
    spot_id = Column(Text, ForeignKey("spots.spot_id"), nullable=False)
    stop_order = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    plan = relationship("Plan", back_populates="stops")
    spot = relationship("Spot")