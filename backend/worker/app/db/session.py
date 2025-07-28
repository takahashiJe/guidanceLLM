# /backend/worker/app/db/session.py

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from worker.app.db.models import Base

# 環境変数からデータベースのURLを取得。なければSQLiteをメモリ上で使用。
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")

# データベースエンジンを作成
# connect_argsはSQLite使用時のみ必要
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}, 
    echo=True
)

# セッションを作成するためのクラス
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# DBセッションを生成・管理するための依存性注入関数
# FastAPIでよく使われるパターンですが、Celeryタスク内でも同様のコンテキスト管理が可能です。
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# 最初に一度だけ、定義したテーブルをすべて作成する関数
def init_db():
    Base.metadata.create_all(bind=engine)
