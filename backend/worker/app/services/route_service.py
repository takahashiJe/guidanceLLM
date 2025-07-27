# /backend/worker/app/services/route_service.py

import json
import os
import math
import networkx as nx
from typing import Dict, Any, Optional, Tuple, List

# --- 設定項目 ---
ROUTE_DATA_PATH = "/code/app/data/chokai_routes.geojson"
AVERAGE_WALKING_SPEED_KMH = 3.0

# --- 内部状態 (初回読み込み時にキャッシュ) ---
_route_graph: Optional[nx.Graph] = None
_node_coordinates: Dict[tuple, str] = {} # キー: 座標タプル, 値: 地名
_location_name_to_coords: Dict[str, tuple] = {} # キー: 地名, 値: 座標タプル
_haraikawa_area_bounds: Optional[Dict[str, float]] = None

# ==================== 初期化処理 ====================

def _load_and_build_graph():
    """
    GeoJSONファイルを読み込み、グラフと各種データを構築する。
    この関数はモジュールの初回インポート時に一度だけ実行される。
    """
    global _route_graph, _node_coordinates, _location_name_to_coords, _haraikawa_area_bounds
    if _route_graph is not None:
        return

    # --- 1. グラフとノード座標の構築 ---
    G = nx.Graph()
    try:
        with open(ROUTE_DATA_PATH, 'r', encoding='utf-8') as f:
            geojson_data = json.load(f)
    except FileNotFoundError:
        print(f"Error: Route data file not found at {ROUTE_DATA_PATH}")
        _route_graph = nx.Graph()
        return

    all_coords = []
    for feature in geojson_data['features']:
        if feature['geometry']['type'] == 'LineString':
            coords = feature['geometry']['coordinates']
            all_coords.extend(coords)
            
            start_node = tuple(coords[0])
            end_node = tuple(coords[-1])
            
            # 地名がpropertiesにある場合のみ辞書に追加
            if 'from' in feature['properties']:
                from_name = feature['properties']['from']
                _node_coordinates[start_node] = from_name
                _location_name_to_coords[from_name] = start_node
            
            if 'to' in feature['properties']:
                to_name = feature['properties']['to']
                _node_coordinates[end_node] = to_name
                _location_name_to_coords[to_name] = end_node

            line_length = 0
            for i in range(len(coords) - 1):
                line_length += haversine_distance(coords[i], coords[i+1])
            
            G.add_edge(start_node, end_node, weight=line_length, properties=feature['properties'])

    _route_graph = G
    print("--- Route graph built successfully. ---")
    # 既知の地名が読み込まれたか確認
    print(f"--- Loaded {len(get_known_locations())} known locations: {get_known_locations()} ---")


    # --- 2. エリア境界の計算 ---
    if not all_coords:
        return
    min_lon = min(coord[0] for coord in all_coords)
    max_lon = max(coord[0] for coord in all_coords)
    min_lat = min(coord[1] for coord in all_coords)
    max_lat = max(coord[1] for coord in all_coords)
    _haraikawa_area_bounds = {
        "min_lon": min_lon, "max_lon": max_lon,
        "min_lat": min_lat, "max_lat": max_lat,
    }
    print(f"--- Haraikawa area bounds calculated: {_haraikawa_area_bounds} ---")

# ==================== ヘルパー関数 ====================

def haversine_distance(coord1: Tuple[float, float], coord2: Tuple[float, float]) -> float:
    """2点間の距離をメートル単位で計算する（ハーベサイン公式）"""
    R = 6371000  # 地球の半径（メートル）
    lon1, lat1 = coord1
    lon2, lat2 = coord2
    
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    
    a = math.sin(dphi / 2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# ==================== 公開サービス関数 ====================

def is_location_within_haraikawa_area(location: Tuple[float, float]) -> bool:
    """指定された位置が祓川エリアの境界内にあるか判定する。"""
    if not _haraikawa_area_bounds:
        return False
    user_lon, user_lat = location
    b = _haraikawa_area_bounds
    return b['min_lon'] <= user_lon <= b['max_lon'] and b['min_lat'] <= user_lat <= b['max_lat']

def get_known_locations() -> List[str]:
    """システムが知っている地名のリストを返す。"""
    return list(_location_name_to_coords.keys())

def get_coords_from_location_name(name: str) -> Optional[Tuple[float, float]]:
    """地名から座標を取得する。"""
    return _location_name_to_coords.get(name)

def find_route_from_coords(start_coords: Tuple[float, float], end_coords: Tuple[float, float]) -> Dict[str, Any]:
    """
    ★★★ 今回新しく追加した、座標ベースのルート検索関数 ★★★
    出発地と目的地の「座標」を受け取り、最適なルートを計算する。
    """
    if _route_graph is None or not _node_coordinates:
        return {"status": "error", "message": "ルートグラフが構築されていません。"}

    # 1. 座標から最も近いグラフ上のノードを見つける（スナップ処理）
    start_node = min(_node_coordinates.keys(), key=lambda node: haversine_distance(start_coords, node))
    end_node = min(_node_coordinates.keys(), key=lambda node: haversine_distance(end_coords, node))
    
    start_name = _node_coordinates.get(start_node, "現在地")
    end_name = _node_coordinates.get(end_node, "目的地")

    # 2. ダイクストラ法で最短経路を探索
    try:
        path_nodes = nx.dijkstra_path(_route_graph, start_node, end_node, weight='weight')
        path_geojson = {"type": "LineString", "coordinates": path_nodes}
        
        total_distance_meters = nx.dijkstra_path_length(_route_graph, start_node, end_node, weight='weight')
        total_distance_km = total_distance_meters / 1000
        estimated_time_hours = total_distance_km / AVERAGE_WALKING_SPEED_KMH
        
        summary = (
            f"{start_name}から{end_name}までのルートです。"
            f"総距離は約{total_distance_km:.1f}km、推定所要時間は約{estimated_time_hours:.1f}時間です。"
        )
        return {"status": "success", "geojson": path_geojson, "summary": summary}

    except nx.NetworkXNoPath:
        return {"status": "error", "message": "指定された区間のルートが見つかりませんでした。"}

# --- モジュール読み込み時にグラフを一度だけ構築 ---
_load_and_build_graph()