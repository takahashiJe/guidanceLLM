# worker/app/services/information/information_service.py

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

# 既存の関数をモジュール直下にも露出（互換維持）
# ※ 遅延インポートはクラス内で行う。ここは tests / 既存コード互換のためエクスポートのみ。
from .weather_api import get_point_forecast  # 一般地点用
from .web_crawler import get_tenkijp_chokai_daily  # 鳥海山ページ（日次）

__all__ = [
    "InformationService",
    # 既存コード互換のためにモジュール関数も公開
    "get_point_forecast",
    "get_tenkijp_chokai_daily",
]

# =========================
# 定数・スコアテーブル
# =========================

MOUNTAIN_TAGS: set[str] = {
    "mountain", "peak", "trail", "hiking", "climb",
    "山", "岳", "登山", "トレッキング", "ハイキング",
}

# 簡易的スコア化（0.0〜1.0）
WEATHER_SCORE_TABLE: Dict[str, float] = {
    # キーは天気API/スクレイパの“状態表現”にある程度ロバストに対応
    "clear": 1.0, "sunny": 1.0, "快晴": 1.0, "晴": 0.9, "晴れ": 0.9,
    "partly_cloudy": 0.8, "cloudy": 0.6, "曇": 0.6, "くもり": 0.6,
    "rain": 0.2, "rainy": 0.2, "小雨": 0.3, "雨": 0.2, "大雨": 0.1,
    "snow": 0.3, "snowy": 0.3, "小雪": 0.4, "雪": 0.3, "大雪": 0.2,
    "storm": 0.1, "雷雨": 0.1,
}

CONGESTION_SCORE_TABLE: Dict[str, float] = {
    "low": 1.0,     # 空いている
    "medium": 0.6,  # ふつう
    "high": 0.2,    # 混んでいる
    "unknown": 0.5, # 不明は中間評価
}

# =========================
# ユーティリティ
# =========================

def _daterange_inclusive(start: date, end: date) -> Iterable[date]:
    d = start
    if end < start:
        return []
    while d <= end:
        yield d
        d += timedelta(days=1)


def _is_mountain_spot(spot: Any) -> bool:
    # Spot.tags が list[str] or comma-separated string を想定。双方に対応。
    tags: List[str] = []
    raw = getattr(spot, "tags", None)
    if isinstance(raw, list):
        tags = [str(t).strip().lower() for t in raw]
    elif isinstance(raw, str):
        tags = [t.strip().lower() for t in raw.split(",")]
    return any(t in MOUNTAIN_TAGS for t in tags)


def _normalize_distance_km(km: float, max_km: float) -> float:
    if max_km <= 0:
        return 0.0
    # 小さい距離を高評価（0〜1）
    v = 1.0 - min(max(km, 0.0) / max_km, 1.0)
    return max(0.0, min(1.0, v))


def _normalize_duration_min(minutes: float, max_min: float) -> float:
    if max_min <= 0:
        return 0.0
    v = 1.0 - min(max(minutes, 0.0) / max_min, 1.0)
    return max(0.0, min(1.0, v))


def _score_weather(weather: Dict[str, Any]) -> float:
    """
    weather から 0.0〜1.0 のスコアへ。
    - まず 'condition' 等の文章ラベルをテーブル変換
    - あれば降水確率、風速、体感温度乖離なども減点（実装済）
    """
    # ラベル → 基本スコア
    label = (weather.get("condition") or weather.get("label") or "").strip().lower()
    base = WEATHER_SCORE_TABLE.get(label, 0.5)

    # 追加ペナルティ
    pop = weather.get("precip_probability")  # 0〜1 or 0〜100
    if isinstance(pop, (int, float)):
        p = float(pop)
        if p > 1.0:
            p = p / 100.0
        base *= max(0.0, 1.0 - 0.6 * p)  # 降水確率が高いほど減点（上限0.6）

    wind = weather.get("wind_speed")  # m/s
    if isinstance(wind, (int, float)) and wind > 8.0:
        base *= 0.8  # 強風でやや減点

    # クリップ
    return max(0.0, min(1.0, base))


def _score_congestion(level: str | None) -> float:
    if not level:
        level = "unknown"
    return CONGESTION_SCORE_TABLE.get(level, 0.5)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    to_rad = math.pi / 180.0
    dlat = (lat2 - lat1) * to_rad
    dlon = (lon2 - lon1) * to_rad
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1 * to_rad) * math.cos(lat2 * to_rad) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


# =========================
# InformationService
# =========================

class InformationService:
    """
    情報取得・評価の統合サービス。
    - DB のスポット検索
    - 天気（山岳/一般地点）
    - ルーティング（距離/所要時間）
    - 混雑情報
    - ナッジ（最適日算出）
    """

    # -----------------------
    # スポット検索系
    # -----------------------
    def find_spots_by_intent(
        self,
        *,
        intent: str,
        query_text: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        intent: 'specific' | 'category' | 'general_tourist'
        query_text: specific の時は名称検索 / general は説明やタグも対象に全文検索
        category: category の時のカテゴリ名
        """
        # 遅延インポート（ワーカー起動安定化）
        from shared.app.database import SessionLocal
        from shared.app.models import Spot  # type: ignore[attr-defined]

        with SessionLocal() as db:
            q = db.query(Spot)

            intent_l = (intent or "").strip().lower()
            if intent_l == "specific" and query_text:
                like = f"%{query_text}%"
                q = q.filter(Spot.official_name.ilike(like))
            elif intent_l == "category" and category:
                like = f"%{category}%"
                # category カラム or tags に含まれる
                q = q.filter(
                    (Spot.category.ilike(like))
                    | (Spot.tags.ilike(like))  # tags が文字列のケースに対応
                )
            else:
                # general：名称・説明・タグを緩く検索
                if query_text:
                    like = f"%{query_text}%"
                    q = q.filter(
                        (Spot.official_name.ilike(like))
                        | (Spot.description.ilike(like))
                        | (Spot.tags.ilike(like))
                    )

            q = q.limit(limit)

            out: List[Dict[str, Any]] = []
            for s in q.all():
                out.append(
                    {
                        "id": getattr(s, "id"),
                        "official_name": getattr(s, "official_name", None),
                        "description": getattr(s, "description", None),
                        "lat": getattr(s, "lat", None),
                        "lon": getattr(s, "lon", None),
                        "tags": getattr(s, "tags", None),
                        "category": getattr(s, "category", None),
                    }
                )
            return out

    def get_spot_details(self, spot_id: int | str) -> Optional[Dict[str, Any]]:
        from shared.app.database import SessionLocal
        from shared.app.models import Spot  # type: ignore[attr-defined]

        with SessionLocal() as db:
            s = db.query(Spot).get(spot_id)  # type: ignore[arg-type]
            if not s:
                return None
            return {
                "id": getattr(s, "id"),
                "official_name": getattr(s, "official_name", None),
                "description": getattr(s, "description", None),
                "lat": getattr(s, "lat", None),
                "lon": getattr(s, "lon", None),
                "tags": getattr(s, "tags", None),
                "category": getattr(s, "category", None),
                "address": getattr(s, "address", None),
                "url": getattr(s, "url", None),
            }

    # -----------------------
    # ナッジ（最適日算出）
    # -----------------------
    def find_best_day_and_gather_nudge_data(
        self,
        *,
        spot_ids: List[int],
        start_date: date,
        end_date: date,
        origin_lat: Optional[float] = None,
        origin_lon: Optional[float] = None,
        lang: str = "ja",
        units: str = "metric",
    ) -> Dict[str, Any]:
        """
        指定スポット群に対して、距離/所要時間・天気・混雑を日毎に集計し、合計スコア最大の日=ベスト日を返す。
        戻り値には日別の詳細（距離・時間・天気・混雑・各スコア）も含める。
        """
        # 遅延インポート
        from shared.app.database import SessionLocal
        from shared.app.models import Spot  # type: ignore[attr-defined]

        # スポットを取得
        with SessionLocal() as db:
            spots: List[Any] = (
                db.query(Spot)
                .filter(Spot.id.in_(spot_ids))  # type: ignore[attr-defined]
                .all()
            )

        if not spots:
            return {
                "best_date": None,
                "days": [],
                "reason": "no_spots",
            }

        # 出発点：未指定なら 1件目の座標
        if origin_lat is None or origin_lon is None:
            first = spots[0]
            origin_lat = getattr(first, "lat", None)
            origin_lon = getattr(first, "lon", None)

        # 山岳かどうか（どれか一つでも山タグなら山岳扱い）
        any_mountain = any(_is_mountain_spot(s) for s in spots)

        # 日ごとの指標入れ物
        day_rows: List[Dict[str, Any]] = []

        # まずルーティング評価のために距離/所要時間の基準（正規化用 max）を求める
        # 全日ではなく “代表1日” で近似し、スポットの総移動距離・時間の上限を計算
        rep_date = start_date
        rep_distance_km, rep_duration_min = self._estimate_trip_distance_duration(
            spots=spots, origin_lat=origin_lat, origin_lon=origin_lon, date_hint=rep_date
        )
        max_distance_km = max(rep_distance_km, 1.0)
        max_duration_min = max(rep_duration_min, 1.0)

        for d in _daterange_inclusive(start_date, end_date):
            # 1) ルーティング
            distance_km, duration_min = self._estimate_trip_distance_duration(
                spots=spots, origin_lat=origin_lat, origin_lon=origin_lon, date_hint=d
            )
            dist_score = _normalize_distance_km(distance_km, max_distance_km)
            dur_score = _normalize_duration_min(duration_min, max_duration_min)

            # 2) 天気
            if any_mountain:
                # 鳥海山のページ情報（サイトが日毎情報を返す想定）
                w = get_tenkijp_chokai_daily(user_agent=None, timeout=10.0)
                # 返却が日毎なら該当日のものへフォーカス、なければ代表値
                weather = _pick_weather_for_date(w, d)
            else:
                # 一般地点：代表として最初のスポット座標を使用
                lat0 = getattr(spots[0], "lat", None)
                lon0 = getattr(spots[0], "lon", None)
                weather = get_point_forecast(
                    lat=lat0, lon=lon0, lang=lang, units=units, extra_params={}
                )
                weather = _pick_weather_for_date(weather, d)

            weather_score = _score_weather(weather or {})

            # 3) 混雑
            congestion_level = self._get_congestion_level(d, spots)
            congestion_score = _score_congestion(congestion_level)

            # 総合スコア（各要素に重みを設定：天気0.5、混雑0.2、距離0.15、時間0.15）
            total = (
                0.50 * weather_score
                + 0.20 * congestion_score
                + 0.15 * dist_score
                + 0.15 * dur_score
            )

            day_rows.append(
                {
                    "date": d.isoformat(),
                    "distance_km": distance_km,
                    "duration_min": duration_min,
                    "distance_score": dist_score,
                    "duration_score": dur_score,
                    "weather": weather,
                    "weather_score": weather_score,
                    "congestion_level": congestion_level,
                    "congestion_score": congestion_score,
                    "total_score": total,
                }
            )

        # ベスト日を決定
        if not day_rows:
            return {"best_date": None, "days": []}
        best = max(day_rows, key=lambda r: r["total_score"])
        return {"best_date": best["date"], "days": day_rows, "best": best}

    # -----------------------
    # 内部：距離/所要時間の推定
    # -----------------------
    def _estimate_trip_distance_duration(
        self,
        *,
        spots: List[Any],
        origin_lat: float,
        origin_lon: float,
        date_hint: date,
    ) -> Tuple[float, float]:
        """
        ルーティングサービスがあれば優先的に使い、無ければハバースィン距離＋簡易歩行速度で代替。
        返り値: (総距離km, 総時間分)
        """
        # 遅延インポート（存在しないケースでも import 時に落とさない）
        routing = None
        try:
            from worker.app.services.routing.routing_service import RoutingService  # type: ignore
            routing = RoutingService()
        except Exception:
            try:
                from worker.app.services.routing import RoutingService  # type: ignore
                routing = RoutingService()
            except Exception:
                routing = None

        # 経由順序：単純に origin → spot1 → spot2 → … の順、成功すれば各辺の距離/時間合計
        waypoints = [(origin_lat, origin_lon)] + [
            (getattr(s, "lat", None), getattr(s, "lon", None)) for s in spots
        ]

        total_km = 0.0
        total_min = 0.0

        if routing is not None:
            # ルーティング API 仕様に幅を持たせる（存在するメソッドへフォールバック）
            def _first_callable(obj, names: List[str]):
                for nm in names:
                    fn = getattr(obj, nm, None)
                    if callable(fn):
                        return fn
                return None

            # 1セグメントずつ問い合わせ
            for (lat1, lon1), (lat2, lon2) in zip(waypoints[:-1], waypoints[1:]):
                if None in (lat1, lon1, lat2, lon2):
                    continue
                # 候補メソッド
                fn = _first_callable(
                    routing,
                    [
                        "estimate_route",         # (lat1,lon1,lat2,lon2)-> {distance_km, duration_min}
                        "route",                  # 返却に distance_m, duration_s が含まれる等
                        "get_route",
                        "compute_route",
                    ],
                )
                seg_km, seg_min = None, None
                if fn:
                    try:
                        resp = fn(lat1, lon1, lat2, lon2)
                        # 返却形に幅を持たせて読み取る
                        if isinstance(resp, dict):
                            if "distance_km" in resp and "duration_min" in resp:
                                seg_km = float(resp["distance_km"])
                                seg_min = float(resp["duration_min"])
                            elif "distance_m" in resp and "duration_s" in resp:
                                seg_km = float(resp["distance_m"]) / 1000.0
                                seg_min = float(resp["duration_s"]) / 60.0
                    except Exception:
                        seg_km, seg_min = None, None

                if seg_km is None or seg_min is None:
                    # ルーティングが無い/読めないときはハバースィン＋簡易速度（徒歩4.5km/h）
                    seg_km = _haversine_km(lat1, lon1, lat2, lon2)
                    seg_min = (seg_km / 4.5) * 60.0

                total_km += seg_km
                total_min += seg_min

            return total_km, total_min

        # ルーティングサービスが無い場合のフォールバック（ハバースィン＋徒歩）
        for (lat1, lon1), (lat2, lon2) in zip(waypoints[:-1], waypoints[1:]):
            if None in (lat1, lon1, lat2, lon2):
                continue
            seg_km = _haversine_km(lat1, lon1, lat2, lon2)
            seg_min = (seg_km / 4.5) * 60.0
            total_km += seg_km
            total_min += seg_min

        return total_km, total_min

    # -----------------------
    # 内部：混雑レベル取得
    # -----------------------
    def _get_congestion_level(self, day: date, spots: List[Any]) -> str:
        """
        Itinerary Service から混雑推定を取得できれば利用。
        無い場合は 'unknown'。
        """
        # 遅延インポート候補を複数試す
        svc = None
        candidates = [
            "worker.app.services.itinerary.service",
            "worker.app.services.itinerary.itinerary_service",
            "worker.app.services.itinerary",
        ]
        for mod_name in candidates:
            try:
                mod = __import__(mod_name, fromlist=["*"])
                svc = getattr(mod, "ItineraryService", None)
                if svc:
                    break
                # 関数群で提供される場合（例：get_congestion_level）
                fn = getattr(mod, "get_congestion_level", None)
                if callable(fn):
                    # スポットID配列で問い合わせできるシンプルAPIを想定
                    spot_ids = [getattr(s, "id", None) for s in spots if getattr(s, "id", None)]
                    level = fn(day, spot_ids)  # type: ignore
                    return str(level or "unknown")
            except Exception:
                continue

        if svc:
            try:
                service = svc()
                # API 仕様に幅を持たせる
                if hasattr(service, "get_congestion_level_for_spots"):
                    spot_ids = [getattr(s, "id", None) for s in spots if getattr(s, "id", None)]
                    level = service.get_congestion_level_for_spots(day, spot_ids)  # type: ignore
                    return str(level or "unknown")
                if hasattr(service, "get_congestion_level"):
                    level = service.get_congestion_level(day)  # type: ignore
                    return str(level or "unknown")
            except Exception:
                pass

        return "unknown"


# ============ ユーティリティ（天気日付抽出） ============

def _pick_weather_for_date(weather_payload: Dict[str, Any] | None, d: date) -> Dict[str, Any]:
    """
    API/スクレイパの返却から日付 d に対応する要素を抽出する。
    フォーマット差を吸収するため、次の優先で探索：
      - payload["daily"] に日次配列があり、各要素に "date" がある
      - payload["days"] に配列がある
      - payload["forecast"] に配列がある
      - いずれも無ければ payload 自体を返す
    """
    if not weather_payload:
        return {}

    iso = d.isoformat()

    def _match(items: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        for it in items:
            dt = it.get("date") or it.get("dt") or it.get("time") or it.get("valid_date")
            if isinstance(dt, str) and dt[:10] == iso:
                return it
        return None

    for key in ("daily", "days", "forecast"):
        arr = weather_payload.get(key)
        if isinstance(arr, list) and arr:
            hit = _match(arr)
            if hit:
                return hit

    # 無ければ代表（最初）かそのまま
    for key in ("daily", "days", "forecast"):
        arr = weather_payload.get(key)
        if isinstance(arr, list) and arr:
            return arr[0]

    return weather_payload


# 便利に使えるデフォルトインスタンス
information_service = InformationService()
