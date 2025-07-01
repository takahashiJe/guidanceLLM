#backend/worker/Dockerfile

FROM python:3.12-slim

RUN apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/*

# 作業ディレクトリの設定
WORKDIR /code

# /codeをPythonの探索パスに追加
ENV PYTHONPATH "${PYTHONPATH}:/code"

RUN apt-get update && apt-get install -y \
    build-essential \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# 依存関係ファイルのコピーとインストール (キャッシュ効率化)
COPY ./backend/worker/requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# --- 開発中は以下のCOPY命令はvolumesで上書きされる ---
# --- 本番イメージ作成時には、これらのコードがイメージに焼き付けられる ---

# # 共有コードのコピー
# COPY ./backend/worker/app /code/app
# COPY ./backend/shared /code/shared

# # データとモデルのコピー
# COPY ./backend/worker/data /code/app/data
# COPY ./backend/scripts /code/scripts

# 起動スクリプトのコピーと権限付与と実行
COPY ./backend/worker/app/entrypoint.sh /code/entrypoint.sh
RUN chmod +x /code/entrypoint.sh
ENTRYPOINT ["/code/entrypoint.sh"]