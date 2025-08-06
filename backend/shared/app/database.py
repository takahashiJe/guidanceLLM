# shared/app/database.py

import os
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

# 1. データベース接続設定の読み込み
# 環境変数からデータベース接続URLを取得。
# 環境変数が設定されていない場合は、デフォルトのローカルDB（docker-composeで起動する想定）に接続する。
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://user:password@db:5432/yamamichi_navi_db")

# 2. データベースエンジンの作成
# `pool_pre_ping=True`は、コネクションプールから接続を取得する際に、
# その接続が有効か（例: DBサーバーが再起動されていないか）をテストするオプション。
# これにより、DB接続が切断された際のエラーを未然に防ぎ、アプリケーションの安定性を高める。
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    echo=False # SQLをコンソールに出力する場合はTrueにする
)

# 3. セッションファクトリの定義
# `sessionmaker`は、新しいSessionオブジェクトを作成するためのファクトリ（クラス）を生成する。
# autocommit=False, autoflush=False とすることで、トランザクション制御を開発者が明示的に行う設定となる。
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 4. セッション管理関数の提供

# --- FastAPIの依存性注入（Dependency Injection）用 ---
def get_db():
    """
    FastAPIのエンドポイント内で使用するDBセッションを生成する。
    リクエストの開始時にセッションを生成し、レスポンス返却後に自動でクローズする。
    使用例:
    @app.get("/items/")
    def read_items(db: Session = Depends(get_db)):
        # dbセッションを使った処理
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Celery Workerやスクリプト実行用 ---
@contextmanager
def session_scope():
    """
    バックグラウンドタスクなど、リクエスト-レスポンスサイクル外でDB操作を行うための
    トランザクショナルなスコープを提供するコンテキストマネージャ。

    - 正常に処理が完了した場合: 自動的に`commit`される。
    - 例外が発生した場合: 自動的に`rollback`される。
    - スコープを抜ける際: 必ず`close`される。

    使用例:
    with session_scope() as db:
        # dbセッションを使った処理
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()