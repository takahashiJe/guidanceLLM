# backend/shared/app/migrations/env.py
# -----------------------------------------------------------------------------
# Alembic の実行エントリ。Base.metadata をターゲットに、オンライン/オフライン
# いずれのモードでもマイグレーションが実行できるようにする。
# - sqlalchemy.url は alembic.ini の %(ALEMBIC_DB_URL)s を優先しつつ、
#   環境変数 ALEMBIC_DB_URL / DATABASE_URL があればそれを上書きする。
# - autogenerate 時にマテビューを誤検出しないよう include_object を調整。
# -----------------------------------------------------------------------------

from __future__ import annotations

import os
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool

# アプリケーションの Base.metadata を取り込む
# PYTHONPATH=backend を前提に、shared.app.models から Base を参照
from shared.app.models import Base  # type: ignore

# Alembic Config オブジェクト。alembic.ini の値にアクセス可能。
config = context.config

# ログ設定（alembic.ini の loggers/handlers/formatters を使用）
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 対象メタデータ
target_metadata = Base.metadata

# DB URL の解決ロジック
# - alembic.ini: sqlalchemy.url = %(ALEMBIC_DB_URL)s
# - 環境変数 ALEMBIC_DB_URL または DATABASE_URL を優先的に適用
def _set_sqlalchemy_url_from_env() -> None:
    env_url = os.getenv("ALEMBIC_DB_URL") or os.getenv("DATABASE_URL")
    if env_url:
        config.set_main_option("sqlalchemy.url", env_url)

_set_sqlalchemy_url_from_env()

# マテリアライズドビューなど、autogenerate 対象から外したいものを制御
EXCLUDE_TABLES = {
    # 解析系のマテビューは手書き SQL で管理。オートジェネ対象外にする
    "congestion_by_date_spot",
    # もし他にも手管理の MV/VIEW があればここに追加
    "spot_congestion_mv",
}

def include_object(
    object_: Any,
    name: str,
    type_: str,
    reflected: bool,
    compare_to: Any,
) -> bool:
    # テーブル型で除外対象に一致するものをスキップ
    if type_ == "table" and name in EXCLUDE_TABLES:
        return False
    return True

def run_migrations_offline() -> None:
    """オフラインモード（接続なし）での実行。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """オンラインモード（実接続あり）での実行。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),  # type: ignore[arg-type]
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()

# モード判定して実行
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
