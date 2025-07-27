# frontend/Dockerfile

FROM python:3.11-slim

WORKDIR /app

# 依存ファイルをコピーしてインストール
COPY ./streamlit_app/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# アプリ本体をコピー
# COPY ./streamlit_app .

# Streamlit アプリを起動（必要に応じて引数追加）
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]

