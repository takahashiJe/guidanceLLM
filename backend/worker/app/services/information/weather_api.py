# worker/app/services/information/weather_api.py

import requests
from typing import Optional, Dict
from datetime import date

def fetch_weather_for_coordinate(latitude: float, longitude: float, target_date: date) -> Optional[Dict[str, str]]:
    """
    Open-Meteo APIを使用して、指定された座標と日付の天気予報を取得する。

    Args:
        latitude (float): 緯度。
        longitude (float): 経度。
        target_date (date): "YYYY-MM-DD"形式の日付。

    Returns:
        Optional[Dict[str, str]]: 天気情報。例: {"weather": "晴れ", "max_temp": "25.0℃"}
    """
    API_URL = "https://api.open-meteo.com/v1/forecast"
    target_date_str = target_date.strftime("%Y-%m-%d")
    
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": "weathercode,temperature_2m_max,temperature_2m_min",
        "timezone": "Asia/Tokyo",
        "start_date": target_date_str,
        "end_date": target_date_str
    }
    try:
        response = requests.get(API_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if not data.get("daily"):
             return None

        weather_code = data["daily"]["weathercode"][0]
        weather_description = _convert_wmo_code_to_description(weather_code)
        
        return {
            "weather": weather_description,
            "max_temp": f"{data['daily']['temperature_2m_max'][0]}℃",
            "min_temp": f"{data['daily']['temperature_2m_min'][0]}℃",
            "source": "Open-Meteo"
        }

    except requests.RequestException as e:
        print(f"Error fetching data from Open-Meteo: {e}")
        return None

def _convert_wmo_code_to_description(code: int) -> str:
    """WMO Weather codeを日本語の簡単な説明に変換する。"""
    if code == 0: return "快晴"
    if code == 1: return "晴れ"
    if code == 2: return "一部曇り"
    if code == 3: return "曇り"
    if 45 <= code <= 48: return "霧"
    if 51 <= code <= 55: return "霧雨"
    if 61 <= code <= 65: return "雨"
    if 66 <= code <= 67: return "みぞれ"
    if 71 <= code <= 75: return "雪"
    if 77 == code: return "雪（霧雪）"
    if 80 <= code <= 82: return "にわか雨"
    if 85 <= code <= 86: return "にわか雪"
    if 95 <= code <= 99: return "雷雨"
    return "不明"