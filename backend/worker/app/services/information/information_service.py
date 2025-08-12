# worker/app/services/information/information_service.py

from typing import List, Optional, Dict, Any
from datetime import date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
import logging

from shared.app.models import Spot
# 他のサービス部やモジュールをインポート
from worker.app.services.routing.routing_service import RoutingService
from worker.app.services.itinerary.itinerary_service import ItineraryService
from worker.app.services.information import crud_spot, web_crawler, weather_api

# ロガーの設定
logger = logging.getLogger(__name__)

# サービスインスタンスの生成 (DIコンテナ等で管理するのが望ましい)
routing_service = RoutingService()
itinerary_service = ItineraryService()

class InformationService:
    """情報提供サービス部のビジネスロジックを実装するサービスクラス。"""

    def find_spots_by_intent(
        self, db: Session, intent_type: str, query: str, language: str = "ja"
    ) -> List[Spot]:
        """[責務1] ユーザーの意図に応じてスポットを検索する。"""
        try:
            if intent_type == "specific":
                return crud_spot.find_spots_by_name(db, name=query, language=language)
            if intent_type == "category":
                return crud_spot.find_spots_by_tag(db, tag=query, language=language)
            if intent_type == "general_tourist":
                return crud_spot.find_spots_by_type(db, spot_type="tourist_spot")
            return []
        except SQLAlchemyError as e:
            logger.error(f"Database error in find_spots_by_intent: {e}", exc_info=True)
            # DBエラー時は空のリストを返し、システムの停止を防ぐ
            return []

    def get_spot_details(self, db: Session, spot_id: str) -> Optional[Spot]:
        """[責務3] スポットの詳細情報（特にsocial_proof）を取得する。"""
        try:
            return crud_spot.get_spot_by_id(db, spot_id=spot_id)
        except SQLAlchemyError as e:
            logger.error(f"Database error in get_spot_details for spot_id {spot_id}: {e}", exc_info=True)
            return None

    def find_best_day_and_gather_nudge_data(
        self,
        db: Session,
        spots: List[Spot],
        user_location: Dict[str, float],
        date_range: Dict[str, date],
    ) -> Dict[str, Dict[str, Any]]:
        """[責務2] 複数のスポットと期間から、ナッジに最適な日と情報を収集・算出する。"""
        
        final_results = {}
        
        for spot in spots:
            try:
                # === [責務2-A] 距離と所要時間を取得 ===
                distance_info = routing_service.get_distance_and_duration(
                    origin=user_location,
                    destination={"latitude": spot.latitude, "longitude": spot.longitude},
                    profile="car"
                )
                # ルート計算失敗時はデフォルト値を設定
                if not distance_info:
                    distance_info = {"distance_km": None, "duration_min": None}

                # === [責務2-B, C] 期間内の日ごとの情報収集と最適日決定 ===
                daily_scores = []
                date_iterator = date_range["start"]
                while date_iterator <= date_range["end"]:
                    
                    weather_info = self._get_weather_for_spot(spot, date_iterator)

                    plan_count = itinerary_service.get_congestion_info(
                        db, spot_id=spot.spot_id, target_date=date_iterator
                    )
                    congestion_status = self._convert_count_to_congestion(plan_count)

                    score = self._calculate_score(weather_info, congestion_status)
                    daily_scores.append({
                        "date": date_iterator,
                        "score": score,
                        "weather": weather_info,
                        "congestion": congestion_status
                    })
                    
                    date_iterator += timedelta(days=1)

                if not daily_scores:
                    continue
                best_day_info = max(daily_scores, key=lambda x: x["score"])

                final_results[spot.spot_id] = {
                    "best_date": best_day_info["date"].strftime("%Y-%m-%d"),
                    "distance_km": distance_info["distance_km"],
                    "duration_min": distance_info["duration_min"],
                    "weather_on_best_date": best_day_info["weather"],
                    "congestion_on_best_date": best_day_info["congestion"],
                }
            except Exception as e:
                # ループ中の特定スポットでエラーが起きても、他のスポットの処理は継続する
                logger.error(f"Error processing nudge data for spot {spot.spot_id}: {e}", exc_info=True)
                continue
            
        return final_results

    def _get_weather_for_spot(self, spot: Spot, target_date: date) -> Dict[str, str]:
        """スポットの特性に応じて最適な天気予報を取得する内部メソッド。失敗してもデフォルト値を返す。"""
        is_mountain = any(tag in spot.tags_ja for tag in ["山", "登山", "ハイキング"])
        
        if is_mountain:
            weather = web_crawler.fetch_chokai_weather_from_tenkijp(target_date)
            if weather:
                return weather

        fallback_weather = weather_api.fetch_weather_for_coordinate(
            spot.latitude, spot.longitude, target_date
        )
        if fallback_weather and is_mountain:
            fallback_weather["note"] = "※これは山麓の予報です"

        return fallback_weather or {"weather": "取得失敗", "source": "N/A"}

    def _convert_count_to_congestion(self, count: int) -> str:
        """計画人数を混雑ステータスに変換する。"""
        if count <= 10: return "空いています"
        if count <= 30: return "比較的穏やかでしょう"
        return "混雑が予想されます"

    def _calculate_score(self, weather_info: Dict[str, str], congestion: str) -> int:
        """天気と混雑度から日ごとのスコアを算出する。"""
        score = 0
        weather = weather_info.get("weather", "")
        
        if "快晴" in weather or "晴れ" in weather: score += 5
        elif "曇り" in weather: score += 2
        
        if "空いています" in congestion: score += 5
        elif "穏やか" in congestion: score += 2
        
        return score
