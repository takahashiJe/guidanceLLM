# backend/worker/app/services/information/information_service.py
# FR-3 の中核：スポット候補検索、ナッジ材料の収集（距離・天気・混雑）と最適日決定、静的情報取得
from __future__ import annotations
from typing import List, Dict, Any, Tuple
from datetime import datetime, timedelta

from sqlalchemy.orm import Session as OrmSession

from shared.app.models import Spot, SpotType
from worker.app.services.information.crud_spot import (
    find_spots_by_official_name, find_spots_by_tag, find_general_tourist_spots
)
from worker.app.services.information.weather_api import WeatherAPI
from worker.app.services.information.web_crawler import TenkiCrawler
from worker.app.services.routing.routing_service import RoutingService
from worker.app.services.itinerary.itinerary_service import ItineraryService

# スコアリング定数（要件に基づく）
WEATHER_SCORE = {
    "晴れ": 5, "快晴": 5,
    "曇り": 3, "晴れ時々曇り": 4, "多云": 3, "阴": 3, "Partly cloudy": 3, "Overcast": 3,
    "雨": 0, "雷雨": 0,
}
CONGESTION_SCORE = {
    "空いています": 5,
    "比較的穏やかでしょう": 3,
    "混雑が予想されます": 0,
}

MOUNTAIN_TAGS = ["山", "登山", "ハイキング", "高山", "稜線", "トレッキング", "peak", "mountain", "hiking"]

class InformationService:
    """情報提供サービス部（Agentic RAG の材料収集を担当）"""

    def __init__(self, db: OrmSession, lang: str = "ja"):
        self.db = db
        self.lang = lang
        self.weather_api = WeatherAPI()
        self.crawler = TenkiCrawler()
        self.routing = RoutingService()
        self.itinerary = ItineraryService(db)

    # ----------------------------------------------------
    # 役務1: 意図に応じた候補スポットのリストアップ
    # ----------------------------------------------------
    def find_spots_by_intent(self, intent_type: str, query: str) -> List[Spot]:
        """
        :param intent_type: "specific" | "category" | "general_tourist"
        :param query: 固有名/カテゴリ語句/曖昧語句
        """
        intent_type = (intent_type or "").strip().lower()
        if intent_type == "specific":
            return find_spots_by_official_name(self.db, query=query, limit=20)
        elif intent_type == "category":
            return find_spots_by_tag(self.db, tag=query, limit=50)
        elif intent_type == "general_tourist":
            return find_general_tourist_spots(self.db, limit=50)
        else:
            # 不明なら無難に tourist_spot 優先で返す
            return find_general_tourist_spots(self.db, limit=50)

    # ----------------------------------------------------
    # 役務2: ナッジ材料の収集と最適日算出
    # ----------------------------------------------------
    def _is_mountain_spot(self, spot: Spot) -> bool:
        if not spot.tags:
            return False
        tag_str = spot.tags.lower()
        return any(t.lower() in tag_str for t in MOUNTAIN_TAGS)

    def _daterange(self, start_str: str, end_str: str) -> List[str]:
        start = datetime.strptime(start_str, "%Y-%m-%d").date()
        end = datetime.strptime(end_str, "%Y-%m-%d").date()
        days = []
        d = start
        while d <= end:
            days.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)
        return days

    def find_best_day_and_gather_nudge_data(
        self,
        spots: List[Spot],
        user_location: Tuple[float, float],
        date_range: Dict[str, str],
    ) -> Dict[int, Dict[str, Any]]:
        """
        :param user_location: (lat, lon) ではなく (lon, lat) に揃えるかはOSRM仕様次第。
                              RoutingService では (lon, lat) を要求するため、ここで変換する。
        :return: { spot_id: {best_date, distance_km, duration_min, weather_on_best_date, congestion_on_best_date} }
        """
        start = date_range.get("start")
        end = date_range.get("end")
        if not (start and end):
            raise ValueError("date_range must contain 'start' and 'end'")

        # OSRM は (lon, lat)。ユーザー位置が (lat, lon) で渡ってきたケースを想定し、明示対応。
        user_lat, user_lon = user_location  # ユーザー入力側の慣例次第。ここでは (lat, lon) 前提。
        user_lonlat = (float(user_lon), float(user_lat))

        dates = self._daterange(start, end)
        result: Dict[int, Dict[str, Any]] = {}

        for sp in spots:
            # A) 距離と所要時間（車移動を基本とする）
            dest_lonlat = (float(sp.longitude), float(sp.latitude))
            dist = self.routing.get_distance_and_duration(origin=user_lonlat, destination=dest_lonlat, profile="car")

            # B) 期間内を走査： 天気 + 混雑
            is_mountain = self._is_mountain_spot(sp)
            day_candidates: List[Dict[str, Any]] = []

            for d in dates:
                # 天気取得：山タグあり→crawler優先、失敗→weather_api
                weather_text = None
                if is_mountain:
                    weather_text = self.crawler.fetch_day_condition(target_date=d, lang=self.lang)
                    if weather_text is None:
                        # フォールバック（※ 山麓の予報である旨の注釈は出力側で明記）
                        w = self.weather_api.get_daily_weather(lat=sp.latitude, lon=sp.longitude, target_date=d, lang=self.lang)
                        weather_text = f"{w['condition']}（※山麓の予報）"
                else:
                    w = self.weather_api.get_daily_weather(lat=sp.latitude, lon=sp.longitude, target_date=d, lang=self.lang)
                    weather_text = w["condition"]

                # 混雑：ItineraryService JOIN 集計
                count = self.itinerary.get_congestion_count(target_spot_id=sp.id, target_date=datetime.strptime(d, "%Y-%m-%d").date())
                congestion_status = self.itinerary.count_to_status(count)

                # スコアリング
                # 天気スコアは括弧注釈を除いた先頭語で推定
                key = weather_text.split("（")[0].split("(")[0].strip()
                w_score = WEATHER_SCORE.get(key, 2)  # 未知語は中間寄せ
                c_score = CONGESTION_SCORE.get(congestion_status, 2)
                total = w_score + c_score

                day_candidates.append({
                    "date": d,
                    "weather": weather_text,
                    "congestion": congestion_status,
                    "score": total,
                })

            # C) 最適日決定
            day_candidates.sort(key=lambda x: (-x["score"], x["date"]))
            best = day_candidates[0] if day_candidates else None

            result[sp.id] = {
                "best_date": best["date"] if best else start,
                "distance_km": dist["distance_km"],
                "duration_min": dist["duration_min"],
                "weather_on_best_date": best["weather"] if best else "",
                "congestion_on_best_date": best["congestion"] if best else "",
                "debug_candidates": day_candidates,  # デバッグ/検証用（必要に応じて外す）
            }

        return result

    # ----------------------------------------------------
    # 役務3: 静的情報の返却
    # ----------------------------------------------------
    def get_spot_details(self, spot_id: int) -> Spot:
        sp = self.db.get(Spot, spot_id)
        if sp is None:
            raise ValueError("Spot not found")
        return sp
