# backend/api_gateway/Dockerfile
FROM python:3.12-slim

WORKDIR /app

# PYTHONPATHの設定
# /appディレクトリをPythonのモジュール検索パスに追加
ENV PYTHONPATH "${PYTHONPATH}:/app"

# 依存関係ファイルのコピーとインストール
COPY ./backend/api_gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 共有コードのコピー
COPY ./backend/shared /app/shared
COPY ./backend/api_gateway/app /app/api_gateway

# ポートの開放
# FastAPIがリッスンするポート8000を開放
EXPOSE 8000

CMD ["uvicorn", "api_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]