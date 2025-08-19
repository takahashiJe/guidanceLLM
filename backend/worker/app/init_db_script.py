# -*- coding: utf-8 -*-
"""
初期化スクリプト（db-init コンテナで実行）
- Alembic マイグレーション (INIT_RUN_ALEMBIC)
- Access Points ロード (INIT_LOAD_ACCESS_POINTS)
- Spots ロード (INIT_LOAD_SPOTS)

環境変数（docker-compose.yml で設定済み）:
  DATABASE_URL
  ALEMBIC_SCRIPT_LOCATION  例: backend/shared/app/migrations
  ALEMBIC_DB_URL           省略可。未設定なら DATABASE_URL を使用
  INIT_RUN_ALEMBIC         "true"/"false" (デフォルト: true)
  INIT_LOAD_ACCESS_POINTS  "true"/"false" (デフォルト: true)
  INIT_LOAD_SPOTS          "true"/"false" (デフォルト: true)

ローダの実体は backend/script 下に配置されている想定:
  - backend/script/load_access_points.py
  - backend/script/load_spots.py
"""

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Dict


def _as_bool(val: str | None, default: bool) -> bool:
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def run(cmd: str, env: Dict[str, str] | None = None) -> None:
    """サブプロセス実行（失敗時は例外で落とす）。"""
    print(f"[init] $ {cmd}", flush=True)
    completed = subprocess.run(shlex.split(cmd), env=env)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> None:
    # --- 環境変数の取得・整備 ---------------------------------------------
    alembic_loc = os.getenv("ALEMBIC_SCRIPT_LOCATION", "backend/shared/app/migrations")
    alembic_ini = Path(alembic_loc) / "alembic.ini"

    alembic_db_url = os.getenv("ALEMBIC_DB_URL") or os.getenv("DATABASE_URL")
    do_alembic = _as_bool(os.getenv("INIT_RUN_ALEMBIC"), True)
    do_ap      = _as_bool(os.getenv("INIT_LOAD_ACCESS_POINTS"), True)
    do_spots   = _as_bool(os.getenv("INIT_LOAD_SPOTS"), True)

    # 実行環境（PYTHONPATH等）は compose 側で与えられているのでそのまま継承
    env = os.environ.copy()

    print("[init] ===== DB Init Settings =====", flush=True)
    print(f"[init] WORKDIR:            {Path.cwd()}", flush=True)
    print(f"[init] ALEMBIC_LOCATION:   {alembic_loc}", flush=True)
    print(f"[init] ALEMBIC_INI:        {alembic_ini}", flush=True)
    print(f"[init] ALEMBIC_DB_URL set: {bool(alembic_db_url)}", flush=True)
    print(f"[init] INIT_RUN_ALEMBIC:   {do_alembic}", flush=True)
    print(f"[init] INIT_LOAD_AP:       {do_ap}", flush=True)
    print(f"[init] INIT_LOAD_SPOTS:    {do_spots}", flush=True)
    print("[init] =============================", flush=True)

    # --- 0) 前提ファイルチェック（分かりやすい失敗にする） ---------------
    if do_alembic and not alembic_ini.is_file():
        print(f"[init][ERROR] alembic.ini が見つかりません: {alembic_ini}", flush=True)
        print("[init][HINT] ALEMBIC_SCRIPT_LOCATION の指定を確認してください。", flush=True)
        raise SystemExit(2)

    # --- 1) Alembic -------------------------------------------------------
    if do_alembic:
        # env.py が ALEMBIC_DB_URL/DATABASE_URL を優先して読みます
        cmd = f'alembic -c "{alembic_ini.as_posix()}" upgrade head'
        run(cmd, env=env)
    else:
        print("[init] Alembic はスキップ (INIT_RUN_ALEMBIC=false)", flush=True)

    # --- 2) Access Points ロード ------------------------------------------
    ap_loader = Path("backend/script/load_access_points.py")
    if do_ap:
        if ap_loader.is_file():
            print("[init] ---- Load Access Points START ----", flush=True)
            run(f'{shlex.quote(sys.executable)} {ap_loader.as_posix()}', env=env)
            print("[init] ---- Load Access Points DONE ----", flush=True)
        else:
            print(f"[init][WARN] {ap_loader} が見つかりません。AP ロードをスキップします。", flush=True)
    else:
        print("[init] Access Points ロードはスキップ (INIT_LOAD_ACCESS_POINTS=false)", flush=True)

    # --- 3) Spots ロード ---------------------------------------------------
    spots_loader = Path("backend/script/load_spots.py")
    if do_spots:
        if spots_loader.is_file():
            print("[init] ---- Load Spots START ----", flush=True)
            run(f'{shlex.quote(sys.executable)} {spots_loader.as_posix()}', env=env)
            print("[init] ---- Load Spots DONE ----", flush=True)
        else:
            print(f"[init][WARN] {spots_loader} が見つかりません。Spots ロードをスキップします。", flush=True)
    else:
        print("[init] Spots ロードはスキップ (INIT_LOAD_SPOTS=false)", flush=True)

    print("[init] ✅ 初期化完了", flush=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as e:
        # そのまま終了コードを返す（compose は exit code で成否を検知）
        raise
    except Exception as e:
        print(f"[init][ERROR] {e}", flush=True)
        sys.exit(1)
