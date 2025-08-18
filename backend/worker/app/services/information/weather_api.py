# worker/app/services/information/weather_api.py
from __future__ import annotations

import datetime as _dt
from typing import Dict, Any, Optional, Tuple
import os

import requests


# --- Open-Meteo weathercode -> Japanese description ---
_WEATHERCODE_JA: Dict[int, str] = {
    0: "快晴",
    1: "晴れ",
    2: "晴れ時々くもり",
    3: "くもり",
    45: "霧",
    48: "霧（霧氷）",
    51: "霧雨（弱い）",
    53: "霧雨（並）",
    55: "霧雨（強い）",
    56: "着氷性の霧雨（弱い）",
    57: "着氷性の霧雨（強い）",
    61: "雨（弱い）",
    63: "雨（並）",
    65: "雨（強い）",
    66: "着氷性の雨（弱い）",
    67: "着氷性の雨（強い）",
    71: "雪（弱い）",
    73: "雪（並）",
    75: "雪（強い）",
    77: "雪あられ",
    80: "にわか雨（弱い）",
    81: "にわか雨（並）",
    82: "にわか雨（激しい）",
    85: "にわか雪（弱い）",
    86: "にわか雪（強い）",
    95: "雷雨（弱い〜並）",
    96: "雷雨（ひょうを伴う可能性：弱い）",
    99: "雷雨（ひょうを伴う可能性：強い）",
}


def weathercode_to_text_ja(code: Optional[int]) -> str:
    """Open-Meteo の weathercode を日本語の天気概況に変換。"""
    if code is None:
        return "天気不明"
    try:
        return _WEATHERCODE_JA.get(int(code), f"天気コード{code}")
    except Exception:
        return "天気不明"


class OpenMeteoClient:
    """
    超軽量の Open-Meteo クライアント。
    - ネットワークアクセスは実行時のみ（import 時には外部接続しない）
    - 既定タイムゾーンは Asia/Tokyo（環境変数 OPEN_METEO_TZ で変更可）
    """

    def __init__(self, base_url: str | None = None, timeout: float = 8.0):
        self.base_url = base_url or os.getenv(
            "OPEN_METEO_BASE",
            "https://api.open-meteo.com/v1/forecast",
        )
        self.timeout = timeout
        self.tz = os.getenv("OPEN_METEO_TZ", "Asia/Tokyo")

    def _request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        resp = requests.get(self.base_url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_daily(
        self,
        lat: float,
        lon: float,
        start: _dt.date,
        end: _dt.date,
    ) -> Dict[str, Any]:
        """
        指定期間のデイリー統計（weathercode, tmax, tmin, precipitation_sum）を取得。
        """
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum",
            "timezone": self.tz,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        return self._request(params)


def _pick_daily_for_date(
    payload: Dict[str, Any],
    target: _dt.date,
) -> Optional[Tuple[Optional[int], Optional[float], Optional[float], Optional[float]]]:
    """
    Open-Meteo の daily.* から target 日の
    (weathercode, tmax, tmin, precip) を取り出す。
    """
    daily = payload.get("daily") or {}
    dates = daily.get("time") or []
    try:
        idx = dates.index(target.isoformat())
    except ValueError:
        return None

    def _pick(key: str):
        arr = daily.get(key) or []
        return arr[idx] if idx < len(arr) else None

    wcode = _pick("weathercode")
    tmax = _pick("temperature_2m_max")
    tmin = _pick("temperature_2m_min")
    precip = _pick("precipitation_sum")
    return (
        int(wcode) if wcode is not None else None,
        float(tmax) if tmax is not None else None,
        float(tmin) if tmin is not None else None,
        float(precip) if precip is not None else None,
    )


def get_point_forecast(lat: float, lon: float, date: _dt.date | str) -> str:
    """
    緯度・経度・日付から日本語の1日予報テキストを返す公開関数。

    information_service.py から呼ばれる前提のため：
      - 例外は外に投げず、失敗時は日本語メッセージの文字列を返す
      - 短い説明文（天気＋気温＋降水量）に整形する
    """
    # date は str（"YYYY-MM-DD" など）でも受け付ける
    if isinstance(date, str):
        try:
            date = _dt.date.fromisoformat(date)
        except ValueError:
            # "YYYY/MM/DD" にも配慮
            date = _dt.datetime.strptime(date, "%Y/%m/%d").date()

    client = OpenMeteoClient()
    try:
        payload = client.get_daily(lat, lon, date, date)
        picked = _pick_daily_for_date(payload, date)
        if not picked:
            return "予報データが見つかりませんでした。"

        wcode, tmax, tmin, precip = picked
        desc = weathercode_to_text_ja(wcode)

        def fmt(v: Optional[float], unit: str) -> str:
            return f"{round(v, 1):.1f}{unit}" if v is not None else f"—{unit}"

        tmax_s = fmt(tmax, "℃")
        tmin_s = fmt(tmin, "℃")
        precip_s = fmt(precip, "mm")

        # 例: "晴れ。最高25.3℃ / 最低16.8℃。降水量は1.4mmの見込み。"
        return f"{desc}。最高{tmax_s} / 最低{tmin_s}。降水量は{precip_s}の見込み。"

    except Exception as e:
        # 呼び出し側で人間向けレスポンスをさらに整える前提のため、
        # ここでは例外送出を避け、簡潔な失敗メッセージを返す
        return f"天気情報の取得に失敗しました（{e.__class__.__name__}）。"


__all__ = [
    "OpenMeteoClient",
    "weathercode_to_text_ja",
    "get_point_forecast",
]
