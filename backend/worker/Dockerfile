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

COPY ./backend/worker/entrypoint.sh /entrypoint.sh
RUN chmod 777 /entrypoint.sh

# CMDではなくENTRYPOINTで起動スクリプトを指定
ENTRYPOINT ["/entrypoint.sh"]