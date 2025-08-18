# backend/shared/app/migrations/versions/20250815_02_add_conversation_embeddings_and_mv.py
# -----------------------------------------------------------------------------
# 目的:
#   1) conversation_embeddings テーブルの追加
#      - mxbai-embed-large のベクトルを float8[] で保持
#      - 検索はアプリ側（Python）でコサイン類似度計算
#   2) congestion_by_date_spot マテリアライズドビューの作成
#      - plans.start_date × stops.spot_id でJOIN集計
#      - UNIQUE INDEX (visit_date, spot_id) を作成し、CONCURRENTLY での REFRESH を可能に
# -----------------------------------------------------------------------------

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as psql

# リビジョン識別子
revision = "20250815_02_add_conversation_embeddings_and_mv"
down_revision = "20250815_01_add_pre_generated_guides"
branch_labels = None
depends_on = None

EMB_TABLE = "conversation_embeddings"
MV_NAME = "congestion_by_date_spot"
MV_UNIQUE_INDEX = "ux_congestion_by_date_spot"

def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # -------------------------------------------------------------------------
    # 1) conversation_embeddings（冪等対応）
    # -------------------------------------------------------------------------
    if EMB_TABLE not in inspector.get_table_names():
        op.create_table(
            EMB_TABLE,
            sa.Column("id", sa.Integer, primary_key=True, nullable=False),
            sa.Column("session_id", sa.String(length=64), nullable=False, index=True),
            sa.Column("speaker", sa.String(length=16), nullable=False),  # "user" | "assistant" | "system"
            sa.Column("lang", sa.String(length=8), nullable=False),
            sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("text", sa.Text, nullable=False),
            sa.Column("embedding_version", sa.String(length=64), nullable=False, server_default=sa.text("'mxbai-embed-large'")),
            # ベクトルは float8[] として保存（pgvector 未使用方針）
            sa.Column("embedding", psql.ARRAY(sa.FLOAT), nullable=False),
        )
        # 検索用の基本インデックス（セッションと時系列）
        op.create_index("ix_conversation_embeddings_session_ts", EMB_TABLE, ["session_id", "ts"], unique=False)

    # -------------------------------------------------------------------------
    # 2) マテリアライズドビュー congestion_by_date_spot（冪等対応）
    # -------------------------------------------------------------------------
    # 既に存在するか確認（Postgres の情報スキーマからは視認しにくいので try/except で作成）
    # 初回作成時はユニークインデックスも作成
    try:
        op.execute(sa.text(f"CREATE MATERIALIZED VIEW {MV_NAME} AS "
                           "SELECT p.start_date::date AS visit_date, s.spot_id, COUNT(DISTINCT p.user_id) AS num_plans "
                           "FROM plans p "
                           "JOIN stops s ON s.plan_id = p.id "
                           "GROUP BY visit_date, s.spot_id"))
        # UNIQUE INDEX（CONCURRENTLY は MV 作成直後は使えないため通常作成）
        op.execute(sa.text(f"CREATE UNIQUE INDEX {MV_UNIQUE_INDEX} ON {MV_NAME} (visit_date, spot_id)"))
    except Exception:
        # 既に存在する場合はスキップ
        pass

def downgrade() -> None:
    # 逆順で削除
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # MV の削除
    try:
        op.execute(sa.text(f"DROP INDEX IF EXISTS {MV_UNIQUE_INDEX}"))
        op.execute(sa.text(f"DROP MATERIALIZED VIEW IF EXISTS {MV_NAME}"))
    except Exception:
        pass

    # conversation_embeddings の削除
    if EMB_TABLE in inspector.get_table_names():
        op.drop_index("ix_conversation_embeddings_session_ts", table_name=EMB_TABLE)
        op.drop_table(EMB_TABLE)
