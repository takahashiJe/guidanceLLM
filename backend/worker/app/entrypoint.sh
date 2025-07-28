#!/bin/sh

export PYTHONPATH="/code"

# --- PostgreSQL の接続チェック ---
echo "Waiting for PostgreSQL to be ready..."
# -h postgres: `postgres`という名前のホストに接続
# -p 5432: 5432番ポートに接続
# -U ${POSTGRES_USER}: 環境変数で指定されたユーザーで接続試行
# このループは、postgresコンテナが接続を受け付けるまで最大30秒間待ちます。
until pg_isready -h postgres -p 5432 -U "${POSTGRES_USER}"; do
  echo "PostgreSQL is unavailable - sleeping"
  sleep 1
done
echo "PostgreSQL is ready!"

echo "Attempting to initialize database..."
python -m app.init_db_script

# Celery起動
echo "Starting Celery worker..."
exec celery -A backend.shared.celery_app worker --loglevel=info -P solo