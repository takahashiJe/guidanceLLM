# /backend/worker/app/services/planning_spot_service.py
# 訪問計画を立てられる場所のリストを提供するサービス

import json
from typing import List

# Dockerコンテナ内のパスを指定
PLANNING_SPOTS_PATH = "/code/app/data/spots_for_planning.json"

# モジュールレベルでスポットのリストをキャッシュするための変数
_plannable_spots: List[str] = []

def _load_spots_from_json():
    """
    アプリケーションの初回起動時に一度だけJSONファイルからスポットを読み込み、
    モジュールレベルの変数にキャッシュする。
    """
    global _plannable_spots
    try:
        with open(PLANNING_SPOTS_PATH, 'r', encoding='utf-8') as f:
            _plannable_spots = json.load(f)
        print(f"--- Successfully loaded {len(_plannable_spots)} plannable spots. ---")
    except FileNotFoundError:
        print(f"!!! WARNING: Planning spots file not found at {PLANNING_SPOTS_PATH}. Planning will not work correctly. !!!")
    except json.JSONDecodeError:
        print(f"!!! ERROR: Failed to decode JSON from {PLANNING_SPOTS_PATH}. The file might be corrupted. !!!")

def get_plannable_spots() -> List[str]:
    """
    計画可能な場所（スポット）の名前のリストを返す。
    このリストはキャッシュされているため、高速にアクセスできます。
    """
    return _plannable_spots

# このモジュールがインポートされた際に、一度だけスポットリストの読み込み処理を実行する
_load_spots_from_json()
