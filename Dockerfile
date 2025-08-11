# (ルート)/Dockerfile

# =================================================================
# 1. Base Stage: 全ステージで共通のベースイメージ
# =================================================================
FROM python:3.11-slim AS base
# 環境変数を設定
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # PYTHONPATHを設定し、/appをモジュール検索パスの起点に追加
    # これにより、どのディレクトリからでも `from backend.` でインポート可能になる
    PYTHONPATH=/app

# 全てのステージで作業ディレクトリを/appに統一
WORKDIR /app


# =================================================================
# 2. Builder Stage: 依存関係をインストールする専用ステージ
# =================================================================
FROM base AS builder
# 依存関係定義ファイルのみを先にコピー
COPY ./backend/pyproject.toml ./backend/poetry.lock* /app/backend/
# backendディレクトリに移動
WORKDIR /app/backend
# Poetryをインストールし、pyproject.tomlに基づき全ての依存関係をインストール
RUN pip install --no-cache-dir poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-root --no-dev


# =================================================================
# 3. Development Stage: 開発環境用イメージ
# =================================================================
FROM base AS development
# builderステージからインストール済みのライブラリのみをコピー
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
# 開発時はソースコードをマウントするため、COPYは不要
# デフォルトコマンドはbash。docker-compose.override.ymlで上書きされる
CMD ["/bin/bash"]


# =================================================================
# 4. Production Stage: 本番環境用イメージ
# =================================================================
FROM base AS production
# builderステージからインストール済みのライブラリのみをコピー
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
# backendディレクトリのソースコードを/app/backendにコピー
COPY ./backend /app/backend
# frontendのビルド済み静的ファイルをコピーする場合（将来的な拡張）
# COPY ./frontend/dist /app/static
