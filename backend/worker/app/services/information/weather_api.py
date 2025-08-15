# backend/worker/app/services/information/weather_api.py
# 天気 API 取得（山麓用 or フォールバック用）
# ここでは無償の Open-Meteo API を利用（APIキー不要）
from __future__ import annotations
from typing import Dict, Any, Optional
import requests

# Open-Meteo の weathercode を日本語/簡体字/英語へ粗くマップ
WEATHER_CODE_MAP_JA = {
    0: "快晴", 1: "晴れ", 2: "晴れ時々曇り", 3: "曇り",
    45: "霧", 48: "霧氷", 51: "霧雨(弱)", 53: "霧雨(中)", 55: "霧雨(強)",
    61: "雨(弱)", 63: "雨(中)", 65: "雨(強)", 66: "着氷性の霧雨", 67: "着氷性の雨",
    71: "雪(弱)", 73: "雪(中)", 75: "雪(強)",
    80: "にわか雨(弱)", 81: "にわか雨(中)", 82: "にわか雨(強)",
    95: "雷雨", 96: "雹を伴う雷雨(弱)", 97: "雹を伴う雷雨(強)"
}
WEATHER_CODE_MAP_EN = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Light rain", 63: "Moderate rain", 65: "Heavy rain", 66: "Freezing drizzle", 67: "Freezing rain",
    71: "Light snow", 73: "Moderate snow", 75: "Heavy snow",
    80: "Light showers", 81: "Moderate showers", 82: "Heavy showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 97: "Thunderstorm with heavy hail"
}
WEATHER_CODE_MAP_ZH = {
    0: "晴", 1: "多云", 2: "间多云", 3: "阴",
    45: "雾", 48: "霜雾", 51: "小毛雨", 53: "中毛雨", 55: "大毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻毛雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    80: "阵雨(小)", 81: "阵雨(中)", 82: "阵雨(大)",
    95: "雷阵雨", 96: "雷阵雨伴小冰雹", 97: "雷阵雨伴大冰雹"
}

def _code_to_text(code: int, lang: str) -> str:
    if lang == "ja":
        return WEATHER_CODE_MAP_JA.get(code, "不明")
    if lang == "zh":
        return WEATHER_CODE_MAP_ZH.get(code, "未知")
    return WEATHER_CODE_MAP_EN.get(code, "Unknown")

class WeatherAPI:
    """Open-Meteo を使って緯度経度のデイリー天気（天気コード）を取得する"""

    BASE_URL = "https://api.open-meteo.com/v1/forecast"

    def get_daily_weather(self, lat: float, lon: float, target_date: str, lang: str = "ja") -> Dict[str, str]:
        """
        :param target_date: "YYYY-MM-DD"
        :return: {"date": target_date, "condition": "晴れ", "source": "open-meteo"}
        """
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "weathercode",
            "timezone": "Asia/Tokyo",
            "start_date": target_date,
            "end_date": target_date,
        }
        r = requests.get(self.BASE_URL, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        try:
            code = data["daily"]["weathercode"][0]
        except Exception:
            return {"date": target_date, "condition": "不明", "source": "open-meteo"}

        return {"date": target_date, "condition": _code_to_text(int(code), lang), "source": "open-meteo"}
