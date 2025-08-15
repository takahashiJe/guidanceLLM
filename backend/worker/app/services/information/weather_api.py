# -*- coding: utf-8 -*-
"""
外部天気API 実装（Open-Meteo 使用、APIキー不要）
- 入力: (lat, lon, date_str="YYYY-MM-DD")
- 出力: {"date": "YYYY-MM-DD", "summary": "晴れ/曇り/雨 など", "source": "api", "note": None}
"""

from __future__ import annotations
import math
from typing import Dict, Optional
import httpx


OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"
# Open-Meteo の weathercode -> 日本語ラベルへの簡易マップ
# 参考: https://open-meteo.com/en/docs
_WEATHERCODE_MAP = {
    0: "快晴",  # Clear sky
    1: "晴れ", 2: "晴れ時々曇り", 3: "曇り",  # Mainly clear / partly cloudy / overcast
    45: "霧", 48: "霧",
    51: "霧雨", 53: "霧雨", 55: "霧雨",
    56: "着氷性の霧雨", 57: "着氷性の霧雨",
    61: "弱い雨", 63: "雨", 65: "強い雨",
    66: "着氷性の雨", 67: "着氷性の雨",
    71: "小雪", 73: "雪", 75: "大雪",
    77: "雪片", 80: "にわか雨", 81: "にわか雨", 82: "激しいにわか雨",
    85: "にわか雪", 86: "にわか雪",
    95: "雷雨", 96: "ひょうを伴う雷雨", 97: "ひょうを伴う雷雨",
    99: "ひょうを伴う激しい雷雨",
}

def _to_label_from_code(code: Optional[int]) -> str:
    if code is None:
        return "不明"
    # 代表値へ
    return _WEATHERCODE_MAP.get(code, "不明")


def get_weather_by_latlon(lat: float, lon: float, date_str: str) -> Dict:
    """
    指定日の日別天気を Open-Meteo で取得する。
    - daily.weathercode を参照、Asia/Tokyo で解釈
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "weathercode",
        "timezone": "Asia/Tokyo",
        "start_date": date_str,
        "end_date": date_str,
    }

    # タイムアウト/リトライ（簡易）
    timeout = httpx.Timeout(connect=5.0, read=8.0)
    for attempt in range(3):
        try:
            with httpx.Client(timeout=timeout, headers={"User-Agent": "guidanceLLM-weather/1.0"}) as client:
                r = client.get(OPEN_METEO_BASE, params=params)
                r.raise_for_status()
                data = r.json()
                daily = data.get("daily", {})
                codes = daily.get("weathercode") or []
                code = codes[0] if codes else None
                label = _to_label_from_code(code)

                # 大まかなラベルを「晴れ/曇り/雨」に寄せたい場合の正規化（InformationService のスコアと整合）
                normalized = _normalize_to_sunny_cloudy_rain(label)

                return {
                    "date": date_str,
                    "summary": normalized,
                    "source": "api",
                    "note": None,
                }
        except Exception:
            if attempt == 2:
                raise  # 呼び出し元で扱う
            continue


def _normalize_to_sunny_cloudy_rain(label: str) -> str:
    """
    スコアリングで使う 3値（晴れ/曇り/雨）へ大まかに丸め込む。
    雪・雷雨などは安全側で「雨」に寄せる。
    """
    if any(k in label for k in ["快晴", "晴れ"]):
        return "晴れ"
    if any(k in label for k in ["曇", "霧"]):
        return "曇り"
    if label == "不明":
        return "曇り"  # 中立寄せ
    # 雨・雪・雷雨・着氷性などは総じて悪条件扱い
    return "雨"


def annotate_foothill(weather: Dict) -> Dict:
    """山岳クロール失敗時の注釈付与"""
    w = dict(weather)
    w["note"] = "※これは山麓に近い一般の予報です（山域は急変に注意）"
    return w
