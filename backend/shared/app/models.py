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
    JSON,
    UUID,
    UUID as UUIDType,
)
import uuid
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func
from geoalchemy2 import Geometry # PostGIS用の型

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    user_id = Column(Integer, primary_key=True)
    username = Column(String(255), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    sessions = relationship("Session", back_populates="user")
    plans = relationship("Plan", back_populates="user")

class Session(Base):
    __tablename__ = "sessions"
    session_id = Column(UUID(as_uuid=True), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    app_status = Column(String(50), nullable=False, default='Browse')
    active_plan_id = Column(Integer, ForeignKey("plans.plan_id"), nullable=True)
    language = Column(String(10), nullable=False, default='ja')
    interaction_mode = Column(String(10), nullable=False, default='text')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated = Column(DateTime(timezone=True), onupdate=func.now())

    user = relationship("User", back_populates="sessions")
    plan = relationship("Plan")
    history = relationship("ConversationHistory", back_populates="session")

class ConversationHistory(Base):
    __tablename__ = "conversation_history"
    message_id = Column(Integer, primary_key=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.session_id"), nullable=False)
    turn = Column(Integer, nullable=False)
    user_input = Column(Text)
    ai_output = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("Session", back_populates="history")

class Spot(Base):
    """
    FR-3: POIマスターデータ
    """
    __tablename__ = "spots"
    spot_id = Column(Text, primary_key=True)
    
    # スポットの種別を管理するカラム
    spot_type = Column(String(50), nullable=False) #'tourist_spot', 'accommodation'
    
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

class Plan(Base):
    __tablename__ = "plans"
    plan_id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    plan_name = Column(String(255))
    start_date = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="plans")
    stops = relationship("Stop", back_populates="plan", order_by="Stop.stop_order")

class Stop(Base):
    __tablename__ = "stops"
    stop_id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("plans.plan_id"), nullable=False)
    spot_id = Column(Text, ForeignKey("spots.spot_id"), nullable=False)
    stop_order = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    plan = relationship("Plan", back_populates="stops")
    spot = relationship("Spot")

class AccessPoint(Base):
    """
    OSMから抽出した、駐車場や登山口などのアクセス拠点情報を格納するテーブル。
    車と徒歩のルートを組み合わせる際の「乗り換え地点」として機能する。
    """
    __tablename__ = "access_points"

    # 内部管理用のUUID主キー
    id = Column(UUIDType(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # OpenStreetMap由来のID。重複を許さず、検索のキーとする。
    osm_id = Column(String(255), unique=True, nullable=False, index=True)
    
    # 'parking' または 'trailhead'
    access_type = Column(String(50), nullable=False, index=True)
    
    name = Column(Text, nullable=True)
    
    # OSMから取得した全てのタグ情報をJSON形式でそのまま保存
    # (例: surface, access, capacity, fee など)
    tags = Column(JSON)

    latitude = Column(Double, nullable=False)
    longitude = Column(Double, nullable=False)
    # PostGISを使った高速な地理空間検索のためのカラム
    geom = Column(Geometry(geometry_type='POINT', srid=4326), nullable=False)

    # OSRMの/nearest APIで事前に計算した、最も近いグラフ上のノードID。
    # ルート計算時に毎回最近傍点を探索するコストを削減するためのキャッシュ。
    car_osrm_node_id = Column(Integer)
    foot_osrm_node_id = Column(Integer)

    # --- 管理用カラム ---
    source = Column(String(50), default="OSM")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    # このデータが人手で確認・修正されたかを示すフラグ
    verified = Column(Boolean, default=False)