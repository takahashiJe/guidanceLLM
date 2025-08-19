# -*- coding: utf-8 -*-
"""
AccessPoints ローダ（冪等）
- access_points.geojson を読んで UPSERT（緯度経度で一意）
- この版では次の修正を反映：
  1) amenity → ap_type マッピングを追加（例: amenity=parking → ap_type="parking"）
  2) 一意判定を (latitude, longitude) ベースに変更（name 依存を廃止）
  3) name 未設定時のデフォルト名を安定生成（AP_<lat>_<lon>）
  4) 既存行がある場合は，空名/unknown のみを上書き（安全更新）
"""
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

from shared.app.database import SessionLocal
from shared.app.models import AccessPoint

# 変更点: パスは従来通り．compose 側で /app/scripts にマウントしておく前提．
AP_GEOJSON = Path("/app/scripts/access_points.geojson")

# 変更点: 緯度経度の丸め桁を定義（同一判定の揺れを抑える）
ROUND_PLACES = 6


def _safe_round_latlon(lon: float, lat: float) -> Tuple[float, float]:
    """緯度経度の丸め．DB照合と挿入を同じ丸め規則に合わせる．"""
    return (round(lon, ROUND_PLACES), round(lat, ROUND_PLACES))


def _infer_ap_type(props: Dict[str, Any]) -> str:
    """
    変更点: amenity / highway などから ap_type を推定するロジックを追加．
    - 明示的に ap_type が与えられていればそれを優先
    - amenity=parking → 'parking'
    - highway=trailhead → 'trailhead'
    - それ以外 → 'unknown'
    """
    explicit = props.get("ap_type")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    amenity = (props.get("amenity") or "").strip().lower()
    highway = (props.get("highway") or "").strip().lower()

    if amenity == "parking":
        return "parking"
    if highway == "trailhead":
        return "trailhead"

    # 将来の拡張: 'trailhead'=yes のようなタグ運用に対応
    if (props.get("trailhead") or "").strip().lower() in {"yes", "true", "1"}:
        return "trailhead"

    return "unknown"


def _build_default_name(lat: float, lon: float) -> str:
    """
    変更点: 無名データが多いため，安定的に逆生成できるデフォルト名を導入．
    """
    lon_r, lat_r = _safe_round_latlon(lon, lat)
    return f"AP_{lat_r}_{lon_r}"


def main() -> None:
    db = SessionLocal()
    try:
        if not AP_GEOJSON.exists():
            raise FileNotFoundError(f"GeoJSON not found: {AP_GEOJSON}")

        with AP_GEOJSON.open("r", encoding="utf-8") as f:
            data = json.load(f)

        # ベーシックなバリデーション
        if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
            raise ValueError("Invalid GeoJSON: expected FeatureCollection")

        features: Iterable[Dict[str, Any]] = data.get("features") or []
        upserted = 0
        skipped = 0

        for feature in features:
            # 最低限の守り
            if not isinstance(feature, dict):
                skipped += 1
                continue

            props: Dict[str, Any] = feature.get("properties") or {}
            geom: Dict[str, Any] = feature.get("geometry") or {}

            if (geom.get("type") != "Point") or not geom.get("coordinates"):
                # 変更点: ここでは過剰実装を避け，Point 以外はスキップ（要件最小）
                skipped += 1
                continue

            # GeoJSON は [lon, lat]
            try:
                lon_raw, lat_raw = geom["coordinates"]
                lon, lat = float(lon_raw), float(lat_raw)
            except Exception:
                skipped += 1
                continue

            lon_r, lat_r = _safe_round_latlon(lon, lat)

            # 変更点: amenity 等から ap_type を推定
            ap_type = _infer_ap_type(props)

            # 変更点: name は未設定なら安定生成
            name: str = (
                (props.get("name") or props.get("title") or "").strip()
            ) or _build_default_name(lat_r, lon_r)

            # 変更点: 一意判定は (latitude, longitude) で行う
            existing: AccessPoint | None = (
                db.query(AccessPoint)
                .filter(
                    AccessPoint.latitude == lat_r,
                    AccessPoint.longitude == lon_r,
                )
                .one_or_none()
            )

            if existing:
                # 変更点: 既存行を安全に更新
                # - name が空（念のため）や自動生成名のままなら，今回の name で上書き
                is_auto_name_old = isinstance(existing.name, str) and existing.name.startswith("AP_")
                if (not existing.name) or is_auto_name_old:
                    existing.name = name

                # - ap_type が未設定/unknown なら，今回の推定で補完
                if (not existing.ap_type) or (existing.ap_type == "unknown"):
                    existing.ap_type = ap_type

                # ここではその他のカラムを安易に変更しない（冪等性維持）
            else:
                # 追加
                ap = AccessPoint(
                    name=name,
                    ap_type=ap_type,
                    latitude=lat_r,
                    longitude=lon_r,
                )
                db.add(ap)

            upserted += 1

        db.commit()
        print(f"[load_access_points] upserted={upserted} skipped={skipped}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
