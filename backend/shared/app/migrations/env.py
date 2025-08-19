# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

# Alembic Config オブジェクト
config = context.config

# ログ設定（alembic.ini 準拠）
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# autogenerate は使わないので metadata は None
target_metadata = None

def _get_db_url() -> str:
    # 優先順位: ALEMBIC_DB_URL > DATABASE_URL > alembic.ini の sqlalchemy.url
    url = os.getenv("ALEMBIC_DB_URL") or os.getenv("DATABASE_URL")
    if url:
        return url
    return config.get_main_option("sqlalchemy.url")

def run_migrations_offline() -> None:
    url = _get_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=False,
        compare_server_default=False,
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    url = _get_db_url()
    connectable = create_engine(
        url, poolclass=pool.NullPool, future=True
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=False,
            compare_server_default=False,
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
