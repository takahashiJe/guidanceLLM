# -*- coding: utf-8 -*-
"""
アプリ全体で共有する SQLAlchemy モデル定義。
- 本ファイルは API Gateway / Worker の双方からインポートされる
- Alembic の target_metadata は Base.metadata を参照（migrations/env.py 側で設定）
- 既存のモデルを壊さず、フェーズ0〜10の追加要件（pre_generated_guides / conversation_embeddings）を含む

ポイント
- pgvector が利用可能なら Vector 型、なければ JSON(List[float]) で埋め込みを保持
- pre_generated_guides は (session_id, spot_id, lang) を一意制約
- 会話履歴 / セッション / 計画 / スポット / アクセスポイントなどのリレーションを付与
"""

from __future__ import annotations

import os
import enum
from datetime import datetime, date
from typing import Optional

from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    Text,
    Date,
    DateTime,
    Float,
    Boolean,
    ForeignKey,
    UniqueConstraint,
    Index,
    Enum as SAEnum,
    JSON,
)
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func
from geoalchemy2 import Geometry 

# ------------------------------------------------------------
# Base
# ------------------------------------------------------------
Base = declarative_base()


# ------------------------------------------------------------
# pgvector 利用可否の判定と型定義
# ------------------------------------------------------------
USE_PGVECTOR = False
Vector = None  # type: ignore[misc]

try:
    # pgvector がインストール済みか確認
    from pgvector.sqlalchemy import Vector  # type: ignore
    USE_PGVECTOR = True
except Exception:
    USE_PGVECTOR = False

# 埋め込みベクトル次元数（mxbai-embed-large は 1024 次元）
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
EMBEDDING_VERSION_DEFAULT = os.getenv("EMBEDDING_VERSION", "mxbai-embed-large@v1")


# ------------------------------------------------------------
# Enum 定義
# ------------------------------------------------------------
class Speaker(str, enum.Enum):
    user = "user"
    assistant = "assistant"
    system = "system"


class SpotType(str, enum.Enum):
    # 必要に応じて拡張（観光スポット・登山口・駐車場・宿泊施設など）
    tourist_spot = "tourist_spot"
    trailhead = "trailhead"
    parking = "parking"
    facility = "facility"
    other = "other"


# ------------------------------------------------------------
# User / Auth 関連
# ------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    preferred_lang = Column(String(8), nullable=True)  # 例: "ja" / "en" / "zh"
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    plans = relationship("Plan", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email}>"


# ------------------------------------------------------------
# セッション / 会話履歴
# ------------------------------------------------------------
class Session(Base):
    """
    アプリ内の対話セッション。id は UUID/ULID 文字列想定（API 層で払い出し）。
    """
    __tablename__ = "sessions"

    id = Column(String(64), primary_key=True)  # UUID/ULID 文字列
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    # 現在のアプリ状態（例: "idle", "information", "planning", "navigating" など）
    app_status = Column(String(64), nullable=True)
    # アクティブな計画（存在しない場合は None）
    active_plan_id = Column(Integer, ForeignKey("plans.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User")
    active_plan = relationship("Plan", foreign_keys=[active_plan_id])
    histories = relationship("ConversationHistory", back_populates="session", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Session id={self.id} user_id={self.user_id} app_status={self.app_status}>"


class ConversationHistory(Base):
    """
    会話の逐次履歴。短期記憶の材料として直近5往復を抽出する。
    """
    __tablename__ = "conversation_history"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(32), nullable=False)  # "user" / "assistant" / "system"
    content = Column(Text, nullable=False)
    lang = Column(String(8), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    session = relationship("Session", back_populates="histories")

    __table_args__ = (
        Index("ix_convhist_session_created", "session_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<ConversationHistory id={self.id} session_id={self.session_id} role={self.role}>"


# ------------------------------------------------------------
# 会話長期記憶（Embedding 保存）
# ------------------------------------------------------------
class ConversationEmbedding(Base):
    """
    長期記憶としての会話埋め込み。
    - pgvector を使える場合は Vector 型、使えない場合は JSON(List[float]) として保存
    - kNN 検索はサービス層（worker/app/services/embeddings.py）で抽象化
    """
    __tablename__ = "conversation_embeddings"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # セッション単位での検索を基本とする（session_id で絞って近傍検索）
    session_id = Column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)

    # 会話の話者とテキスト
    speaker = Column(SAEnum(Speaker), nullable=False, index=True)
    lang = Column(String(8), nullable=True)
    text = Column(Text, nullable=False)

    # タイムスタンプ（時系列での再構成やフィルタ用）
    ts = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    # 埋め込みモデルのバージョン（後方互換維持のため）
    embedding_version = Column(String(64), nullable=False, default=EMBEDDING_VERSION_DEFAULT)

    if USE_PGVECTOR and Vector is not None:
        # pgvector(Vector) 型
        embedding = Column(Vector(EMBEDDING_DIM), nullable=False)
        __table_args__ = (
            Index("ix_convemb_session_ts", "session_id", "ts"),
            # pgvector 専用のインデックス（IVFFlat）。初回は Alembic 側で CREATE INDEX USING ivfflat を推奨
            # ここでは btree 以外の作成は Alembic/SQL 側で行うことを前提とし、モデル側では通常 Index のみ。
        )
    else:
        # フォールバック（JSON の float 配列）
        embedding = Column(JSON, nullable=False)  # 例: [0.01, -0.23,]
        __table_args__ = (
            Index("ix_convemb_session_ts", "session_id", "ts"),
        )

    session = relationship("Session")

    def __repr__(self) -> str:
        return f"<ConversationEmbedding id={self.id} session_id={self.session_id} speaker={self.speaker.value}>"


# ------------------------------------------------------------
# スポット / アクセスポイント関連
# ------------------------------------------------------------
class Spot(Base):
    """
    観光スポット等の静的情報（Information Service の主要データ源）。
    - tags は JSON 配列（例: ["waterfall", "山", "ハイキング"]）
    """
    __tablename__ = "spots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    official_name = Column(String(255), nullable=False, index=True)
    spot_type = Column(SAEnum(SpotType), nullable=False, index=True, default=SpotType.tourist_spot)

    # タグ配列：カテゴリ検索（category intent）に利用
    tags = Column(JSON, nullable=True)  # List[str]

    # 位置情報
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)

    # ガイド文作成に必要な静的情報
    description = Column(Text, nullable=True)
    social_proof = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # リレーション
    stops = relationship("Stop", back_populates="spot")

    __table_args__ = (
        Index("ix_spots_type_name", "spot_type", "official_name"),
    )

    def __repr__(self) -> str:
        return f"<Spot id={self.id} name={self.official_name} type={self.spot_type.value}>"


class AccessPoint(Base):
    """
    アクセスポイント（駐車場や登山口などの起点）。
    - AccessPoint テーブルはオーケストレーターのルート分割判断（車→徒歩）に活用
    """
    __tablename__ = "access_points"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, index=True)
    ap_type = Column(String(64), nullable=True)  # 例: "parking", "trailhead"
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<AccessPoint id={self.id} name={self.name}>"
    
    # 追加1: PostGIS の geometry(Point,4326) 列
    #   ・ST_* 関数や KNN での最近傍探索を可能にする
    #   ・ALEMBIC で後付けできるよう、null 許容でまず追加し、移行時に埋める運用でも良い
    geom = Column(Geometry(geometry_type="POINT", srid=4326), nullable=True)

    __table_args__ = (
        # 追加2: (latitude, longitude) の一意制約
        #   ・ローダ側の upsert が name ではなく lat/lon をキーにするための裏付け
        UniqueConstraint("latitude", "longitude", name="uq_access_points_lat_lon"),

        # 追加3: PostGIS GiST インデックス（geom 用）
        #   ・KNN: ORDER BY geom <-> ST_SetSRID(ST_MakePoint(...),4326) を高速化
        Index("ix_access_points_geom", "geom", postgresql_using="gist"),
    )

# ------------------------------------------------------------
# 計画（Plan）/ 立寄り順序（Stop）
# ------------------------------------------------------------
class Plan(Base):
    """
    周遊計画。滞在時間の概念は持たず、Stops の順序のみ管理（FR-4）。
    """
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    start_date = Column(Date, nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="plans")
    stops = relationship("Stop", back_populates="plan", order_by="Stop.order_index", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_plans_user_date", "user_id", "start_date"),
    )

    def __repr__(self) -> str:
        return f"<Plan id={self.id} user_id={self.user_id} start_date={self.start_date}>"


class Stop(Base):
    """
    計画の訪問先（順序リスト）。
    """
    __tablename__ = "stops"

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey("plans.id", ondelete="CASCADE"), nullable=False, index=True)
    spot_id = Column(Integer, ForeignKey("spots.id", ondelete="CASCADE"), nullable=False, index=True)

    # 訪問順序（0,1,2,）
    order_index = Column(Integer, nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    plan = relationship("Plan", back_populates="stops")
    spot = relationship("Spot", back_populates="stops")

    __table_args__ = (
        UniqueConstraint("plan_id", "order_index", name="uq_stops_plan_order"),
        Index("ix_stops_plan_order", "plan_id", "order_index"),
    )

    def __repr__(self) -> str:
        return f"<Stop id={self.id} plan_id={self.plan_id} spot_id={self.spot_id} idx={self.order_index}>"


# ------------------------------------------------------------
# 事前生成ガイド（FR-5-4-1）
# ------------------------------------------------------------
class PreGeneratedGuide(Base):
    """
    ナビ開始時などにスポットごとのガイド文を事前生成して保持。
    - 一意性: (session_id, spot_id, lang)
    """
    __tablename__ = "pre_generated_guides"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(String(64), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    spot_id = Column(Integer, ForeignKey("spots.id", ondelete="CASCADE"), nullable=False, index=True)
    lang = Column(String(8), nullable=False, index=True)
    text = Column(Text, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    session = relationship("Session")
    spot = relationship("Spot")

    __table_args__ = (
        UniqueConstraint("session_id", "spot_id", "lang", name="uq_pre_guides_session_spot_lang"),
        Index("ix_pre_guides_spot_lang", "spot_id", "lang"),
    )

    def __repr__(self) -> str:
        return f"<PreGeneratedGuide id={self.id} session_id={self.session_id} spot_id={self.spot_id} lang={self.lang}>"


# ------------------------------------------------------------
# 参考: 混雑マテビューは Alembic / 初期化 SQL 側で管理
# - congestion_by_date_spot / spot_congestion_mv 等
# - ここでは Model クラスを定義しない（読み取りは生SQL or text() で実施）
# ------------------------------------------------------------
