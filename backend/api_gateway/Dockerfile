# backend/api_gateway/Dockerfile
FROM python:3.12-slim

WORKDIR /code
ENV PYTHONPATH "${PYTHONPATH}:/code"

# 依存関係ファイルのコピーとインストール
COPY ./backend/api_gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# # /code配下にapp, sharedパッケージとしてコピー
# COPY ./backend/api_gateway/app /code/app
# COPY ./backend/shared /code/shared

# ポートの開放
# FastAPIがリッスンするポート8000を開放
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]