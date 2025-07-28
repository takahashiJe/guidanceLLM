# backend/worker/Dockerfile
# ==================================
# 1. ベースイメージ・開発環境 (development stage)
# ==================================
# 今は使われていないDockerfile
FROM python:3.11-slim AS development

RUN apt-get update && apt-get install -y --no-install-recommends \
    netcat-traditional \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PYTHONPATH /app

WORKDIR /app

COPY ./backend/worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt && \
    python -m spacy download ja_core_news_sm --no-deps

# ==================================
# 2. 本番環境 (production stage)
# ==================================
FROM development AS production

# workerとshared、scriptディレクトリのみをコピーする
COPY ./backend/worker /app/worker
COPY ./backend/shared /app/shared
COPY ./script /app/script

# 必要なソースコードをすべてコピー
COPY ./backend /app/backend

# Celeryワーカーを起動
# PYTHONPATHが/app/backendに設定されているため、shared.celery_appとして認識される
COPY ./backend/worker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["celery", "-A", "shared.celery_app.celery_app", "worker", "--loglevel=info", "--pool=threads", "--concurrency=1"]