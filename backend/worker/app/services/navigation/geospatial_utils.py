# worker/app/services/navigation/geospatial_utils.py

from typing import Dict, Any
from shapely.geometry import Point, LineString
from pyproj import Geod

# WGS84測地系に基づく距離計算機
geod = Geod(ellps="WGS84")

def calculate_distance_from_route(
    point_coords: Dict[str, float],
    route_geojson: Dict[str, Any]
) -> float:
    """
    指定された点から、ルート(LineString)までの最短距離（垂線距離）を計算する。

    Args:
        point_coords (Dict[str, float]): ユーザーの現在地。例: {"latitude": 39.1, "longitude": 140.0}
        route_geojson (Dict[str, Any]): ルートのGeoJSONオブジェクト。

    Returns:
        float: ルートからの逸脱距離（メートル単位）。
    """
    try:
        user_point = Point(point_coords["longitude"], point_coords["latitude"])
        route_line = LineString(route_geojson["coordinates"])
        
        # Shapelyのprojectとinterpolateを使って、線上で最も近い点を見つけ、その点と現在地の距離を計算する
        nearest_point_on_route = route_line.interpolate(route_line.project(user_point))
        
        # 2点間の測地線距離を計算
        _, _, distance_meters = geod.inv(
            user_point.x, user_point.y,
            nearest_point_on_route.x, nearest_point_on_route.y
        )
        return distance_meters
    except (KeyError, IndexError, TypeError) as e:
        print(f"Error calculating distance from route: {e}")
        return float('inf') # エラー時は大きな値を返す

def calculate_distance_between_points(
    point1_coords: Dict[str, float],
    point2_coords: Dict[str, float]
) -> float:
    """
    2点間の直線距離（測地線距離）を計算する。

    Args:
        point1_coords (Dict[str, float]): 点1の座標。
        point2_coords (Dict[str, float]): 点2の座標。

    Returns:
        float: 2点間の距離（メートル単位）。
    """
    try:
        _, _, distance_meters = geod.inv(
            point1_coords["longitude"], point1_coords["latitude"],
            point2_coords["longitude"], point2_coords["latitude"]
        )
        return distance_meters
    except KeyError as e:
        print(f"Error calculating distance between points: {e}")
        return float('inf')

def is_within_radius(
    point1_coords: Dict[str, float],
    point2_coords: Dict[str, float],
    radius_meters: float
) -> bool:
    """
    2点間の距離が、指定された半径の内側にあるかどうかを判定する。

    Args:
        point1_coords (Dict[str, float]): 点1の座標。
        point2_coords (Dict[str, float]): 点2の座標。
        radius_meters (float): 判定する半径（メートル単位）。

    Returns:
        bool: 半径内であればTrue、そうでなければFalse。
    """
    distance = calculate_distance_between_points(point1_coords, point2_coords)
    return distance <= radius_meters