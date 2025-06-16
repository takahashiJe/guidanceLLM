FROM python:3.12-slim

# 2. 作業ディレクトリの設定
WORKDIR /app

# 3. PYTHONPATHの設定
# /appディレクトリをPythonのモジュール検索パスに追加します。
ENV PYTHONPATH "${PYTHONPATH}:/app"

# 4. 依存関係ファイルのコピーとインストール
# workerは重いライブラリを含むため、このステップには時間がかかります。
COPY ./backend/worker/requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# 5. 共有コードのコピー
# workerもapi_gatewayと同様に、共通のsharedディレクトリをコピーします。
COPY ./backend/shared /app/shared

# 6. アプリケーションコードのコピー
# worker自身のコアロジックを含むコードをコピーします。
COPY ./backend/worker /app/worker

# 7. コンテナ起動時に実行するコマンド
# ★★★ ここがapi_gatewayとの決定的な違い ★★★
# uvicorn(Webサーバー)ではなく、Celeryのワーカープロセスを起動します。
# これにより、コンテナはRabbitMQを監視し、タスクを受け取って処理を開始します。
CMD ["celery", "-A", "shared.celery_app", "worker", "--loglevel=info"]