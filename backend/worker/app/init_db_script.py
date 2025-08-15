# backend/worker/app/init_db_script.py
import time
import os
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

# sharedディレクトリ内のモデル定義をインポート
# PYTHONPATH=/app が設定されているため、このインポートが可能
from shared.app.models import Base

def wait_for_db():
    """データベースが起動するまで待機する"""
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL environment variable not set.")

    engine = create_engine(db_url)
    retries = 20
    delay = 5
    for i in range(retries):
        try:
            connection = engine.connect()
            connection.close()
            print("Database is ready!")
            return engine
        except OperationalError as e:
            print(f"Database not ready yet, waiting... ({e})")
            time.sleep(delay)
    raise Exception("Database did not become ready in time.")

def main():
    print("Starting database initialization...")
    engine = wait_for_db()

    with engine.connect() as connection:
        # PostGIS拡張機能を有効化する
        print("Enabling PostGIS extension...")
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
        connection.commit() # トランザクションをコミット
        print("PostGIS extension enabled.")

    print("Creating tables...")
    # shared/app/models.pyで定義された全てのテーブルを作成
    Base.metadata.create_all(bind=engine)
    print("Tables created successfully.")

if __name__ == "__main__":
    main()