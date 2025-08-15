# backend/shared/app/models.py
# ※ SQLAlchemy モデル定義。アプリ全体で参照されるスキーマをここで一元管理します。
# - 既存の database.py（SessionLocal, Base）と連携する想定
# - 既存のコードから参照されるカラム名/リレーションに合わせて定義
# - Alembic を導入する前提であっても、初期段階ではこの定義で動作するように実装

from __future__ import annotations
from datetime import datetime, date
from typing import Optional, List

from sqlalchemy import (
    Column, Integer, String, DateTime, Date, ForeignKey,
    Text, Float, Enum, Boolean, Index, UniqueConstraint
)
from sqlalchemy.orm import relationship, Mapped, mapped_column, declarative_base
import enum

Base = declarative_base()

# ------------------------------------------------------------
# 列挙型の定義
# ------------------------------------------------------------

class AppStatus(str, enum.Enum):
    browse = "Browse"
    planning = "planning"
    navigating = "navigating"

class SpotType(str, enum.Enum):
    tourist_spot = "tourist_spot"
    accommodation = "accommodation"

class AccessPointType(str, enum.Enum):
    parking = "parking"
    trailhead = "trailhead"
    others = "others"


# ------------------------------------------------------------
# ユーザー・認証まわり
# ------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # logical relations
    sessions: Mapped[List["Session"]] = relationship("Session", back_populates="user", cascade="all, delete-orphan")
    plans: Mapped[List["Plan"]] = relationship("Plan", back_populates="user", cascade="all, delete-orphan")


# ------------------------------------------------------------
# 会話セッション管理
# ------------------------------------------------------------

class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # フロント生成の一意なID（localStorage に保持）を string で受ける
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    user: Mapped["User"] = relationship("User", back_populates="sessions")

    current_status: Mapped[AppStatus] = mapped_column(Enum(AppStatus), default=AppStatus.browse, nullable=False)
    active_plan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("plans.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # relations
    active_plan: Mapped[Optional["Plan"]] = relationship("Plan", foreign_keys=[active_plan_id])
    histories: Mapped[List["ConversationHistory"]] = relationship("ConversationHistory", back_populates="session", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_sessions_user_session", "user_id", "session_id"),
    )


class ConversationHistory(Base):
    __tablename__ = "conversation_histories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # "user" | "assistant" | "system"
    # ユーザー入力ではない自動ガイド等は本文の代わりに「SYSTEM_TRIGGER」を記録
    content: Mapped[Text] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    session: Mapped["Session"] = relationship("Session", back_populates="histories")


# ------------------------------------------------------------
# スポット・アクセスポイント
# ------------------------------------------------------------

class Spot(Base):
    __tablename__ = "spots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    official_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    social_proof: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 位置情報
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    # タグはカンマ区切りのシンプルな文字列として保持（PostgreSQL の Array でもよい）
    tags: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    spot_type: Mapped[SpotType] = mapped_column(Enum(SpotType), default=SpotType.tourist_spot, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # relations
    stops: Mapped[List["Stop"]] = relationship("Stop", back_populates="spot")


class AccessPoint(Base):
    __tablename__ = "access_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    ap_type: Mapped[AccessPointType] = mapped_column(Enum(AccessPointType), default=AccessPointType.parking, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    # どの Spot へ向かうための AP か（NULL 許容：汎用駐車場など）
    spot_id: Mapped[Optional[int]] = mapped_column(ForeignKey("spots.id"), nullable=True)
    spot: Mapped[Optional["Spot"]] = relationship("Spot")

    # 最近傍判定のためのインデックス
    __table_args__ = (
        Index("ix_access_points_lat_lon", "latitude", "longitude"),
    )


# ------------------------------------------------------------
# 計画（滞在時間の概念は持たず、順序リストのみ）
# ------------------------------------------------------------

class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    user: Mapped["User"] = relationship("User", back_populates="plans")

    # 計画の開始日（混雑集計のキー）
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    stops: Mapped[List["Stop"]] = relationship("Stop", back_populates="plan", cascade="all, delete-orphan", order_by="Stop.order_index")


class Stop(Base):
    __tablename__ = "stops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), nullable=False, index=True)
    spot_id: Mapped[int] = mapped_column(ForeignKey("spots.id"), nullable=False, index=True)

    order_index: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    plan: Mapped["Plan"] = relationship("Plan", back_populates="stops")
    spot: Mapped["Spot"] = relationship("Spot", back_populates="stops")

    __table_args__ = (
        UniqueConstraint("plan_id", "order_index", name="uq_stops_plan_order"),
        Index("ix_stops_plan_order", "plan_id", "order_index"),
    )


# ------------------------------------------------------------
# 事前生成ガイド（ナビ開始時に作成・参照）
# ------------------------------------------------------------

class PreGeneratedGuide(Base):
    __tablename__ = "pre_generated_guides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False, index=True)
    spot_id: Mapped[int] = mapped_column(ForeignKey("spots.id"), nullable=False, index=True)
    lang: Mapped[str] = mapped_column(String(8), nullable=False)  # "ja" | "en" | "zh"
    text: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # relations（必要に応じて参照）
    session: Mapped["Session"] = relationship("Session")
    spot: Mapped["Spot"] = relationship("Spot")