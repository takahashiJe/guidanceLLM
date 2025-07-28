# Dockerfile
FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # backendディレクトリをPythonの検索パスの起点とする
    PYTHONPATH=/app

WORKDIR /app

FROM base AS builder
COPY ./backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS development
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
CMD ["/bin/bash"] # 開発時はコマンドを上書きするため、ダミーCMDを配置

FROM base AS production-api
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
# backendディレクトリの中身を/appにコピー
COPY ./backend /app

FROM base AS production-worker
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
# backendディレクトリの中身を/appにコピー
COPY ./backend /app