# backend/shared/app/database.py
import os
import time
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.exc import OperationalError

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")

# --------------- エンジン生成（壊れにくい設定） ---------------
_engine_opts = {}
_connect_args = {}

if DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}
else:
    # PostgreSQL / psycopg2 用の堅牢化オプション
    _engine_opts.update(
        dict(
            pool_pre_ping=True,   # 死んだ接続を自動で捨てる
            pool_recycle=1800,    # 30分で再作成（NAT/ALB 越え対策）
            pool_size=5,
            max_overflow=10,
        )
    )

def _create_engine_with_retry(url: str, retries: int = 5, wait: float = 1.0):
    """
    初回 DNS/接続レースに備えて軽いリトライを実施。
    """
    last_err = None
    for _ in range(retries):
        try:
            eng = create_engine(url, connect_args=_connect_args, **_engine_opts)
            if not url.startswith("sqlite"):
                # exec_driver_sql を使って軽く疎通チェック
                with eng.connect() as conn:
                    conn.exec_driver_sql("SELECT 1")
            return eng
        except OperationalError as e:
            last_err = e
            time.sleep(wait)
    # 最終的にダメでもエンジンを返す（pool_pre_ping で後続が再試行）
    try:
        return create_engine(url, connect_args=_connect_args, **_engine_opts)
    except Exception:
        if last_err:
            raise last_err
        raise

engine = _create_engine_with_retry(DATABASE_URL)

# scoped_session を使う場合は remove() で破棄するのが正解
SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))

def get_db() -> Iterator:
    """
    FastAPI 依存関係：各リクエストでセッションを供給。
    scoped_session のため close() ではなく remove() を呼ぶ。
    """
    try:
        yield SessionLocal()
    finally:
        # スレッド/タスクローカルに紐づくセッションを完全破棄
        SessionLocal.remove()
