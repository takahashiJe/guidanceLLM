# -*- coding: utf-8 -*-
"""
グローバルpytestフィクスチャ:
- DBセッション（ロールバック保証）
- OSRM到達性チェック
- 近傍AP 1点取得ヘルパ
"""
from __future__ import annotations

import os
import socket
import contextlib
from typing import Iterator, Optional, Tuple

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

# アプリのDBユーティリティを使えるならそれを優先
try:
    from shared.app.database import SessionLocal  # app本体と同じ設定
    _USE_APP_SESSIONLOCAL = True
except Exception:
    SessionLocal = None  # type: ignore
    _USE_APP_SESSIONLOCAL = False

# -------- DBセッション（テストごとにロールバック） --------
@pytest.fixture(scope="function")
def db_session() -> Iterator[Session]:
    """
    1テスト=1トランザクション。テスト終了時に確実にロールバック。
    - アプリの SessionLocal があればそれを利用
    - なければ DATABASE_URL から engine を作る
    """
    if _USE_APP_SESSIONLOCAL and SessionLocal is not None:
        session: Session = SessionLocal()
        trans = session.begin()
        try:
            yield session
        finally:
            # テスト内でcommitされていても、このbegin()に対するrollbackで大枠は戻せる
            with contextlib.suppress(Exception):
                trans.rollback()
            session.close()
    else:
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            pytest.skip("DATABASE_URL not set")
        engine = create_engine(db_url)
        TestingSessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        session = TestingSessionLocal()
        trans = session.begin()
        try:
            yield session
        finally:
            with contextlib.suppress(Exception):
                trans.rollback()
            session.close()

# -------- OSRM到達性チェック --------
def _can_connect(host: str, port: int, timeout: float = 1.5) -> bool:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except Exception:
            return False

def _parse_host_port(url: str) -> Optional[Tuple[str,int]]:
    # 例: http://osrm-car:5000 → ("osrm-car", 5000)
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        host = u.hostname or ""
        port = u.port or (80 if u.scheme == "http" else 443)
        return (host, int(port))
    except Exception:
        return None

@pytest.fixture(scope="session")
def osrm_ready() -> bool:
    """
    OSRM(driving/foot) の双方がlistenしているか軽くチェック。
    到達できなければ OSRM 依存テストを skip するために使う。
    """
    car_url = os.environ.get("OSRM_DRIVING_URL")
    foot_url = os.environ.get("OSRM_FOOT_URL")
    if not car_url or not foot_url:
        return False
    ok = True
    for u in (car_url, foot_url):
        hp = _parse_host_port(u)
        if not hp or not _can_connect(*hp):
            ok = False
            break
    return ok

# -------- 近傍のAPを1点ひっぱる補助 --------
@pytest.fixture(scope="function")
def any_access_point(db_session: Session) -> Optional[tuple[int, str, str, float, float]]:
    """
    access_points テーブルから1点返す (id, name, ap_type, lat, lon)
    なければ None
    """
    row = db_session.execute(
        text("""
            SELECT id, name, ap_type::text, latitude, longitude
            FROM access_points
            WHERE ap_type IN ('parking','trailhead')
            ORDER BY id ASC
            LIMIT 1
        """)
    ).first()
    if not row:
        return None
    return (int(row[0]), str(row[1]), str(row[2]), float(row[3]), float(row[4]))
