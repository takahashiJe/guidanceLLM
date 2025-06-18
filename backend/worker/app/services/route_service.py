# /backend/worker/app/services/route_service.py

import json
import os
import networkx as nx
from shapely.geometry import Point, LineString
from typing import Dict, Any, Optional, Tuple, List

# --- 設定項目 ---
ROUTE_DATA_PATH = "/code/app/data/chokai_routes.geojson"
OFF_ROUTE_THRESHOLD_METERS = 30
CHECKPOINT_PROXIMITY_METERS = 15
AVERAGE_WALKING_SPEED_KMH = 3.0

# --- 内部状態 (初回読み込み時にキャッシュ) ---
_route_graph = None
_node_coordinates = {}

def _build_graph_from_geojson():
    """
    GeoJSONファイルを読み込み、NetworkXグラフを構築する。
    """
    global _route_graph, _node_coordinates
    if _route_graph is not None:
        return

    G = nx.Graph()
    try:
        with open(ROUTE_DATA_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"Error: Route data file not found at {ROUTE_DATA_PATH}")
        _route_graph = nx.Graph() # 空のグラフを初期化
        return

    for feature in data['features']:
        if feature['geometry']['type'] == 'LineString':
            coords = feature['geometry']['coordinates']
            start_node = tuple(coords[0])
            end_node = tuple(coords[-1])
            start_name = feature['properties'].get('start_name', str(start_node))
            end_name = feature['properties'].get('end_name', str(end_node))
            
            _node_coordinates[start_node] = start_name
            _node_coordinates[end_node] = end_name

            line = LineString(coords)
            length_meters = line.length * 111320 # 簡易的な度からメートルへの変換
            G.add_edge(start_node, end_node, weight=length_meters, properties=feature['properties'])

    _route_graph = G
    print("--- Route graph built successfully. ---")

def get_known_locations() -> List[str]:
    """
    システムが知っている（GeoJSONに定義されている）地名のリストを返す。
    """
    if _route_graph is None:
        _build_graph_from_geojson()
    return list(set(_node_coordinates.values()))


def get_route_from_service(start_point: str, end_point: str) -> Dict[str, Any]:
    """
    正規化された出発地と目的地の名称から最適ルートを計算する。
    """
    if _route_graph is None:
        _build_graph_from_geojson()

    start_node = next((coord for coord, name in _node_coordinates.items() if start_point == name), None)
    end_node = next((coord for coord, name in _node_coordinates.items() if end_point == name), None)

    if not start_node or not end_node:
        return {"error": "unsupported_location", "message": "出発地または目的地がサポートされていないか、見つかりませんでした。"}

    try:
        path_nodes = nx.dijkstra_path(_route_graph, start_node, end_node, weight='weight')
        path_geojson = {"type": "LineString", "coordinates": path_nodes}
        
        total_distance_meters = nx.dijkstra_path_length(_route_graph, start_node, end_node, weight='weight')
        total_distance_km = total_distance_meters / 1000
        estimated_time_hours = total_distance_km / AVERAGE_WALKING_SPEED_KMH
        
        summary = (
            f"{start_point}から{end_point}までのルートです。"
            f"総距離は約{total_distance_km:.1f}km、推定所要時間は約{estimated_time_hours:.1f}時間です。"
        )
        return {"geojson": path_geojson, "summary": summary}

    except nx.NetworkXNoPath:
        return {"error": "no_path_found", "message": "指定された区間のルートが見つかりませんでした。"}

_build_graph_from_geojson()