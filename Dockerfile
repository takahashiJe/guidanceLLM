# (ルート)/Dockerfile

# =================================================================
# 1. Base Stage: 共通のベースイメージ
# =================================================================
FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# 作業ディレクトリを/appに設定
WORKDIR /app

# =================================================================
# 2. Builder Stage: 依存関係をインストール
# =================================================================
FROM base AS builder
# backendディレクトリをコピー
COPY ./backend /app/backend
# backendディレクトリに移動し、プロジェクトをインストール
WORKDIR /app/backend
# pyproject.tomlに基づき、全ての依存関係をインストール
RUN pip install --no-cache-dir ".[api,worker,dev]"

# =================================================================
# 3. Development Stage: 開発環境用
# =================================================================
FROM base AS development
# builderからライブラリをコピー
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
# builderからインストール済みのプロジェクトをコピー（キャッシュのため）
COPY --from=builder /app/backend /app/backend
CMD ["/bin/bash"]

# =================================================================
# 4. Production Stage: 本番環境用
# =================================================================
FROM base AS production
# builderからライブラリをコピー
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
# backendディレクトリのコードを/app/backendにコピー
COPY ./backend /app/backend