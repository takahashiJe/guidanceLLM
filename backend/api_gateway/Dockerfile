# backend/api_gateway/Dockerfile
# ==================================
# 1. ベースイメージ・開発環境 (development stage)
# ==================================
# 今は使われていないDockerfile
FROM python:3.11-slim AS development

WORKDIR /app

# Pythonがモジュールを探し始める場所を/app/backendに設定
ENV PYTHONPATH="/app"
ENV PYTHONUNBUFFERED 1
ENV PYTHONDONTWRITEBYTECODE 1

COPY ./backend/api_gateway/requirements.txt /app/requirements.txt
# 依存関係をインストールする前に、全ての種類のbcryptを強制的にアンインストールする
RUN pip install --no-cache-dir --upgrade pip && \
    pip uninstall -y bcrypt py-bcrypt && \
    pip install --no-cache-dir -r /app/requirements.txt

# ==================================
# 2. 本番環境 (production stage)
# ==================================
FROM development AS production

# api_gatewayとshared、scriptディレクトリのみをコピーする
COPY ./backend/api_gateway /app/api_gateway
COPY ./backend/shared /app/shared
COPY ./script /app/script

# entrypointもこちらで定義する
COPY ./backend/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]

# デフォルトコマンド。uvicornは/appからapi_gatewayモジュールを見つけられる
CMD ["uvicorn", "api_gateway.app.main:app", "--host", "0.0.0.0", "--port", "8000"]