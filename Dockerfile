# =================================================================
# 1. Base Stage: 共通のベースイメージ
# =================================================================
FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # インポートの基点を /app/backend に設定
    PYTHONPATH=/app/backend

WORKDIR /app

# =================================================================
# 2. Builder Stage: 依存関係をインストール
# =================================================================
FROM base AS builder
# まずpyproject.tomlだけをコピーして依存関係をインストールする
# これにより、コードを変更しても毎回pip installが走るのを防ぐ
COPY ./backend/pyproject.toml .
RUN pip install --no-cache-dir ".[api,worker,dev]"
# プロジェクト全体（apiとworkerの両方）の依存関係をインストール
RUN pip install --no-cache-dir ".[api,worker]"

# =================================================================
# 3. Development Stage: 開発環境用
# =================================================================
FROM base AS development
# builderステージからインストール済みのライブラリをコピー
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
# 開発時はホットリロードのためCMDは上書きされる
CMD ["/bin/bash"]

# =================================================================
# 4. Production Stage: 本番環境用（apiとworkerで共通）
# =================================================================
FROM base AS production
# builderステージからインストール済みのライブラリをコピー
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
# backendディレクトリの中身を/appにコピー
COPY ./backend /app