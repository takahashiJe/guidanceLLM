# -*- coding: utf-8 -*-
"""
Information Service 本体（本実装版天気/クロール呼び出しに対応）
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Tuple
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from shared.app.database import SessionLocal
from shared.app import models

from worker.app.services.information import crud_spot
from worker.app.services.information import weather_api
from worker.app.services.information import web_crawler
from worker.app.services.routing.routing_service import RoutingService
from worker.app.services.itinerary.itinerary_service import ItineraryService


# ---- スコアリング定数 ----
WEATHER_SCORE = {"晴れ": 5, "曇り": 3, "雨": 0}
CONGESTION_SCORE = {"low": 5, "mid": 3, "high": 0}
CONGESTION_THRESHOLDS = (10, 30)  # (low_max, mid_max)


def _score_congestion(count: int):
    low_max, mid_max = CONGESTION_THRESHOLDS
    if count <= low_max:
        return "空いています", CONGESTION_SCORE["low"]
    if count <= mid_max:
        return "比較的穏やかでしょう", CONGESTION_SCORE["mid"]
    return "混雑が予想されます", CONGESTION_SCORE["high"]


def _date_range_iter(start: str, end: str) -> List[str]:
    s = datetime.fromisoformat(start).date()
    e = datetime.fromisoformat(end).date()
    days = (e - s).days
    return [(s + timedelta(days=i)).isoformat() for i in range(days + 1)]


@dataclass
class SpotLite:
    id: int
    lat: float
    lon: float
    tags: str
    spot_type: str


class InformationService:
    def __init__(self):
        self.routing = RoutingService()
        self.itinerary = ItineraryService()

    def find_spots_by_intent(
        self,
        *,
        intent_type: Literal["specific", "category", "general_tourist"],
        query: Optional[str],
        language: Literal["ja", "en", "zh"] = "ja",
        limit: int = 30,
    ) -> List[models.Spot]:
        with SessionLocal() as db:
            if intent_type == "specific" and query:
                return crud_spot.find_spots_by_official_name(db, query, language, limit)
            if intent_type == "category" and query:
                return crud_spot.find_spots_by_tag(db, query, limit)
            return crud_spot.list_general_tourist_spots(db, limit)

    def find_best_day_and_gather_nudge_data(
        self,
        *,
        spots: List[models.Spot],
        user_location: Tuple[float, float],
        date_range: Dict[str, str],
    ) -> Dict[int, Dict]:
        if not spots:
            return {}

        lat0, lon0 = float(user_location[0]), float(user_location[1])
        start, end = date_range["start"], date_range["end"]
        days = _date_range_iter(start, end)

        results: Dict[int, Dict] = {}
        with SessionLocal() as db:
            for sp in spots:
                lite = SpotLite(
                    id=sp.id, lat=float(sp.latitude), lon=float(sp.longitude),
                    tags=sp.tags or "", spot_type=sp.spot_type or "tourist_spot"
                )

                # A) 距離/時間（車）
                dist_km, dur_min = self.routing.get_distance_and_duration(
                    origin=(lat0, lon0),
                    destination=(lite.lat, lite.lon),
                    profile="car",
                )

                # B) 日ごとの天気・混雑 → スコア
                best_score = -1
                best_payload = None
                is_mountain = any(tag in lite.tags for tag in ["山", "登山", "ハイキング", "mountain", "hiking"])

                for d in days:
                    # --- 天気 ---
                    if is_mountain:
                        # 1) 山岳ページをクロール
                        try:
                            w = web_crawler.get_mountain_weather_chokai(d)
                            weather_label = w["summary"]
                        except Exception:
                            # 2) 失敗時は平地APIでフォールバック + 注釈
                            try:
                                w_api = weather_api.get_weather_by_latlon(lite.lat, lite.lon, d)
                                w_api = weather_api.annotate_foothill(w_api)
                                weather_label = w_api["summary"]
                            except Exception:
                                weather_label = "曇り"  # 最後の保険（中立寄せ）
                    else:
                        # 平地：Open-Meteo
                        try:
                            w = weather_api.get_weather_by_latlon(lite.lat, lite.lon, d)
                            weather_label = w["summary"]
                        except Exception:
                            weather_label = "曇り"  # API障害時の保険

                    weather_score = WEATHER_SCORE.get(weather_label, 2)

                    # --- 混雑 ---
                    count = self.itinerary.get_plan_count_for_spot_on_date(db, spot_id=lite.id, date_str=d)
                    congestion_label, congestion_score = _score_congestion(count)

                    total = weather_score + congestion_score
                    if total > best_score:
                        best_score = total
                        best_payload = {
                            "best_date": d,
                            "distance_km": dist_km,
                            "duration_min": dur_min,
                            "weather_on_best_date": weather_label,
                            "congestion_on_best_date": congestion_label,
                        }

                if best_payload:
                    results[lite.id] = best_payload

        return results

    def get_spot_details(self, spot_id: int) -> Optional[models.Spot]:
        with SessionLocal() as db:
            return crud_spot.get_spot_by_id(db, spot_id)
