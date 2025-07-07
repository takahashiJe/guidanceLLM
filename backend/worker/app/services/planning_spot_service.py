# /backend/worker/app/services/planning_spot_service.py
# 訪問計画を立てられる場所のリストを提供するサービス

import json
from typing import List, Dict, Any, Optional
from thefuzz import process

# Dockerコンテナ内のパスを指定
POI_DATA_PATH = "/code/app/data/POI.json"

# POIデータをキャッシュする変数
_poi_data: List[Dict[str, Any]] = []
_spot_name_to_id_map: Dict[str, Dict[str, str]] = {}

def _load_and_build_poi_cache():
    """
    POI.jsonを読み込み、高速な検索のためのキャッシュを構築する。
    """
    global _poi_data, _spot_name_to_id_map
    try:
        with open(POI_DATA_PATH, 'r', encoding='utf-8') as f:
            _poi_data = json.load(f)
        
        # 正規化のための辞書を構築
        # {"ja": {"法体の滝": "spot_007", ...}, "en": {"Hottai Waterfall": "spot_007", ...}}
        for lang in ["ja", "en", "zh"]:
            _spot_name_to_id_map[lang] = {}
            for spot in _poi_data:
                # 公式名
                if spot.get("official_name", {}).get(lang):
                    _spot_name_to_id_map[lang][spot["official_name"][lang]] = spot["spot_id"]
                # エイリアス（別名）
                for alias in spot.get("aliases", {}).get(lang, []):
                    _spot_name_to_id_map[lang][alias] = spot["spot_id"]

        print(f"--- Successfully loaded and cached {len(_poi_data)} POIs. ---")
    except FileNotFoundError:
        print(f"!!! ERROR: POI data file not found at {POI_DATA_PATH}. !!!")
    except json.JSONDecodeError:
        print(f"!!! ERROR: Failed to decode JSON from {POI_DATA_PATH}. !!!")

def normalize_spot_by_language(spot_name_input: str, language: str) -> Optional[Dict[str, Any]]:
    """
    ユーザーが入力したスポット名と現在の言語を元に、最も一致するPOIオブジェクトを返す。
    """
    if not _poi_data or language not in _spot_name_to_id_map:
        return None

    # 指定された言語の「名前->ID」辞書を取得
    lang_specific_map = _spot_name_to_id_map.get(language, {})
    
    # あいまい検索で最も近い名前を見つける
    best_match, score = process.extractOne(spot_name_input, lang_specific_map.keys())

    if score > 80: # 80点以上なら一致とみなす
        spot_id = lang_specific_map[best_match]
        # IDを元に完全なPOIオブジェクトを返す
        return next((spot for spot in _poi_data if spot["spot_id"] == spot_id), None)
    
    return None

# 起動時に一度だけキャッシュを構築
_load_and_build_poi_cache()
