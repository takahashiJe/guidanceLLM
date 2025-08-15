# backend/worker/app/services/information/information_service.py
# -------------------------------------------------------------
# 情報提供サービス部の本実装。
# - 候補スポット抽出（意図別）
# - ナッジ材料収集（距離・天気・混雑・最適日）
# - 静的情報返却
#
# 依存：
# - DB: shared.app.database.get_db / shared.app.models
# - ルート計算: worker.app.services.routing.routing_service.RoutingService
# - 混雑集計: worker.app.services.itinerary.itinerary_service.get_congestion_info
# - 天気取得: 同ディレクトリの web_crawler / weather_api
# -------------------------------------------------------------

from __future__ import annotations

from typing import Dict, List, Literal, Optional, Tuple
from datetime import datetime, date, timedelta

from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func

from shared.app.models import Spot  # Spot モデル（official_name, tags, spot_type, latitude, longitude, description, social_proof 等）
from worker.app.services.routing.routing_service import RoutingService
from worker.app.services.itinerary.itinerary_service import get_congestion_info
from .web_crawler import get_tenkijp_chokai_daily  # 山岳用：tenki.jp 鳥海山ページのスクレイパ
from .weather_api import get_point_forecast  # 一般地点用：緯度経度からの天気API

# -------------------------------
# 設定・定数
# -------------------------------

# 「山岳スポット」判定に使うタグ（tags にこれらの語が含まれれば山として扱う）
MOUNTAIN_TAGS = {"山", "登山", "ハイキング", "トレッキング", "山岳", "峰", "縦走"}

# 天気スコアリング（簡易）
# 備考：tenki.jp / API 双方からの文字列を「部分一致」で判定できるよう幅広めに設定
WEATHER_SCORE_TABLE = [
    (("快晴",), 5),
    (("晴", "晴れ"), 5),
    (("薄曇", "曇", "くもり"), 3),
    (("小雨", "にわか雨", "一時雨"), 1),
    (("雨",), 0),
    (("雪", "みぞれ", "吹雪"), 0),
]

# 混雑スコアリング（Itinerary Service の status に合わせる）
CONGESTION_SCORE_TABLE = {
    "空いています": 5,
    "比較的穏やかでしょう": 3,
    "混雑が予想されます": 0,
}


# -------------------------------
# ユーティリティ
# -------------------------------

def _daterange_inclusive(start_d: date, end_d: date):
    """開始〜終了を**両端含む**日付反復"""
    cur = start_d
    while cur <= end_d:
        yield cur
        cur = cur + timedelta(days=1)


def _is_mountain_spot(tags: Optional[str]) -> bool:
    """「山タグ」を含むかを判定（tags はカンマ区切り想定／NULL許容）"""
    if not tags:
        return False
    t = tags.lower()
    for key in MOUNTAIN_TAGS:
        if key.lower() in t:
            return True
    return False


def _score_weather(desc: str) -> int:
    """天気記述のスコアを返す（部分一致ベースの簡易判定）"""
    if not desc:
        return 0
    d = desc.strip()
    for keys, score in WEATHER_SCORE_TABLE:
        for k in keys:
            if k in d:
                return score
    # どれにも該当しない場合は中間値相当
    return 3


def _score_congestion(status: str) -> int:
    """混雑ステータスのスコア"""
    return CONGESTION_SCORE_TABLE.get(status, 3)


def _normalize_distance_km(meters: float) -> float:
    return round((meters or 0.0) / 1000.0, 1)


def _normalize_duration_min(seconds: float) -> int:
    return int(round((seconds or 0.0) / 60.0))


# -------------------------------
# 候補スポット抽出
# -------------------------------

def find_spots_by_intent(
    db: Session,
    intent_type: Literal["specific", "category", "general_tourist"],
    query: Optional[str],
    language: Literal["ja", "en", "zh"] = "ja",
    limit: int = 50,
) -> List[Spot]:
    """
    ユーザー意図に応じた候補スポット抽出（FR-3-1, FR-3-5）
    - specific: official_name 部分一致
    - category: tags に query を含む
    - general_tourist: spot_type = 'tourist_spot' 固定
    """
    q = db.query(Spot)

    if intent_type == "specific":
        if not query:
            return []
        # official_name の部分一致（大文字小文字は DB コレーションに依存。ilike が使えればそれが理想）
        like = f"%{query}%"
        q = q.filter(Spot.official_name.ilike(like))
        return q.limit(limit).all()

    elif intent_type == "category":
        if not query:
            return []
        like = f"%{query}%"
        # tags（カンマ区切り or フリーテキスト想定）への部分一致
        q = q.filter(Spot.tags.ilike(like))
        return q.limit(limit).all()

    elif intent_type == "general_tourist":
        # 宿泊施設が出ないよう観光スポット固定
        q = q.filter(Spot.spot_type == "tourist_spot")
        return q.limit(limit).all()

    else:
        # 未知の intent は空
        return []


# -------------------------------
# 静的情報の取得
# -------------------------------

def get_spot_details(db: Session, spot_id: int) -> Optional[Spot]:
    """
    指定 spot の静的情報を返す（official_name / description / social_proof 等）
    """
    return db.query(Spot).filter(Spot.id == spot_id).first()


# -------------------------------
# ナッジ材料の収集と最適日の算出
# -------------------------------

def find_best_day_and_gather_nudge_data(
    db: Session,
    spots: List[Spot],
    user_location: Tuple[float, float],
    date_range: Dict[str, str],
    travel_profile: Literal["car", "foot"] = "car",
) -> Dict[int, Dict]:
    """
    プロアクティブ・ナッジ（FR-3-3）の材料を収集し、最適日を算出して返す。
    返却フォーマット（例）:
    {
      123: {
        "best_date": "2025-08-10",
        "distance_km": 21.4,
        "duration_min": 38,
        "weather_on_best_date": "晴れ",
        "congestion_on_best_date": "空いています",
        "congestion_count_on_best_date": 4,
        "per_day": { "2025-08-09": {...}, "2025-08-10": {...}, ... }
      },
      ...
    }
    """
    if not spots:
        return {}

    # 日付範囲の解釈
    start_str = date_range.get("start")
    end_str = date_range.get("end")
    if not (start_str and end_str):
        raise ValueError("date_range は {'start': 'YYYY-MM-DD', 'end': 'YYYY-MM-DD'} 形式で指定してください。")

    start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
    end_date = datetime.strptime(end_str, "%Y-%m-%d").date()

    # ルート計算クライアント（同期）
    routing = RoutingService()

    results: Dict[int, Dict] = {}

    for spot in spots:
        # A) 距離・所要時間（不変情報）
        try:
            distance_m, duration_s = routing.get_distance_and_duration(
                origin=user_location,
                destination=(spot.latitude, spot.longitude),
                profile=travel_profile,
            )
            distance_km = _normalize_distance_km(distance_m)
            duration_min = _normalize_duration_min(duration_s)
        except Exception:
            # ルート計算が失敗した場合でも他情報で続行
            distance_km, duration_min = 0.0, 0

        # B) 期間内の日ごとの情報を収集
        is_mountain = _is_mountain_spot(spot.tags)
        per_day: Dict[str, Dict] = {}

        for d in _daterange_inclusive(start_date, end_date):
            d_str = d.strftime("%Y-%m-%d")

            # --- 天気 ---
            weather_desc = ""
            weather_note = None  # 山麓予報の注釈用

            if is_mountain:
                # まず tenki.jp 鳥海山ページからスクレイプ
                try:
                    weather_desc = get_tenkijp_chokai_daily(d)
                except Exception:
                    # 失敗時フォールバック：地点予報＋注釈
                    try:
                        weather_desc = get_point_forecast(spot.latitude, spot.longitude, d)
                        weather_note = "※ 山頂の詳細予報取得に失敗したため、山麓付近の一般予報を代替表示しています。"
                    except Exception:
                        weather_desc = ""
                        weather_note = "※ 天気情報の取得に失敗しました。"
            else:
                # 麓スポットは地点予報で十分
                try:
                    weather_desc = get_point_forecast(spot.latitude, spot.longitude, d)
                except Exception:
                    weather_desc = ""
                    weather_note = "※ 天気情報の取得に失敗しました。"

            weather_score = _score_weather(weather_desc)

            # --- 混雑 ---
            try:
                cong = get_congestion_info(db=db, spot_id=spot.id, visit_date=d)
                congestion_status = cong.get("status", "比較的穏やかでしょう")
                congestion_count = int(cong.get("count", 0))
            except Exception:
                # 失敗時は中間値で継続
                congestion_status = "比較的穏やかでしょう"
                congestion_count = 0

            congestion_score = _score_congestion(congestion_status)

            per_day[d_str] = {
                "weather": weather_desc,
                "weather_score": weather_score,
                "weather_note": weather_note,
                "congestion": congestion_status,
                "congestion_count": congestion_count,
                "congestion_score": congestion_score,
                "total_score": weather_score + congestion_score,
            }

        # C) 最適日の決定
        best_key = None
        best_total = -1
        for k, v in per_day.items():
            if v["total_score"] > best_total:
                best_total = v["total_score"]
                best_key = k

        # 返却整形
        if best_key is None:
            # すべて失敗した場合のフォールバック
            best_key = start_date.strftime("%Y-%m-%d")

        best_day_obj = per_day[best_key]

        results[spot.id] = {
            "best_date": best_key,
            "distance_km": distance_km,
            "duration_min": duration_min,
            "weather_on_best_date": best_day_obj["weather"],
            "congestion_on_best_date": best_day_obj["congestion"],
            "congestion_count_on_best_date": best_day_obj["congestion_count"],
            "per_day": per_day,  # UI 側で比較表示したい場合に利用可
        }

    return results
