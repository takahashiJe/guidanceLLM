# -*- coding: utf-8 -*-
"""
Alembic のエントリポイント。
- SQLAlchemy のメタデータを取り込み、Online/Offline 両モードに対応。
- DATABASE_URL は .env から shared.app.database 経由で読み込み。
"""
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# モデルのメタデータ（自動検出に使う）
from shared.app.models import Base  # 全テーブルのメタデータ

# Alembic Config オブジェクト
config = context.config

# .ini 経由のログ設定
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ターゲットメタデータ
target_metadata = Base.metadata

# 環境変数から DB URL を取得し、alembic.ini の sqlalchemy.url を上書き
db_url = os.getenv("ALEMBIC_DB_URL") or os.getenv("DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline():
    """オフラインモード: DB接続を作らずに SQL を生成"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        transactional_ddl=True,
        compare_type=True,   # 変更検出を型まで
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """オンラインモード: 実 DB に接続してマイグレーション実行"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
