# -*- coding: utf-8 -*-
"""
AccessPoints ローダ（冪等）
- access_points.geojson を読んで UPSERT（緯度経度で一意）
- この版では次の修正を反映：
  1) amenity → ap_type マッピングを追加（例: amenity=parking → ap_type="parking"）
  2) 一意判定を (latitude, longitude) ベースに変更（name 依存を廃止）
  3) name 未設定時のデフォルト名を安定生成（AP_<lat>_<lon>）
  4) 既存行がある場合は，空名/自動生成名のみを上書き（安全更新）

追加の整合調整（今回の修正点）:
  A) GeoJSON の既定パスを /app/backend/scripts に修正（__file__ 基準）
     - さらに AP_GEOJSON 環境変数で上書き可能（運用を楽に）
  B) ap_type の既定値を DB ENUM（parking, trailhead, other）に合わせて "other" に統一
     - 以前の "unknown" は使わない
  C) 挿入・更新後に geom を必ず埋める UPDATE を追加（KNN/GiST を確実に有効化）
"""

import json
import os  # 追加: 環境変数でパス上書きに対応
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

from sqlalchemy import text  # 追加: geom 一括更新に使用
from shared.app.database import SessionLocal
from shared.app.models import AccessPoint

# 変更点A: 既定パスをスクリプトのあるディレクトリに変更し、ENVで上書き可能に
DEFAULT_GEOJSON = Path(__file__).parent / "access_points.geojson"
AP_GEOJSON = Path(os.getenv("AP_GEOJSON", DEFAULT_GEOJSON.as_posix()))

# 既存ロジック: 緯度経度の丸め桁
ROUND_PLACES = 6


def _safe_round_latlon(lon: float, lat: float) -> Tuple[float, float]:
    """緯度経度の丸め．DB照合と挿入を同じ丸め規則に合わせる．"""
    return (round(lon, ROUND_PLACES), round(lat, ROUND_PLACES))


def _infer_ap_type(props: Dict[str, Any]) -> str:
    """
    amenity / highway などから ap_type を推定するロジック。
    - 明示的に ap_type が与えられていればそれを優先
    - amenity=parking → 'parking'
    - highway=trailhead または trailhead=yes/true/1 → 'trailhead'
    - それ以外 → 'other'   ← 変更点B: ENUM と整合（unknown は使わない）
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

    if (props.get("trailhead") or "").strip().lower() in {"yes", "true", "1"}:
        return "trailhead"

    return "other"  # ← ここを "other" に統一


def _build_default_name(lat: float, lon: float) -> str:
    """無名データが多いため，安定的に逆生成できるデフォルト名を導入。"""
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
                # 要件最小: Point 以外はスキップ
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

            ap_type = _infer_ap_type(props)

            # name は未設定なら安定生成
            name: str = (
                (props.get("name") or props.get("title") or "").strip()
            ) or _build_default_name(lat_r, lon_r)

            # 一意判定は (latitude, longitude)
            existing: AccessPoint | None = (
                db.query(AccessPoint)
                .filter(
                    AccessPoint.latitude == lat_r,
                    AccessPoint.longitude == lon_r,
                )
                .one_or_none()
            )

            if existing:
                # 既存行を安全に更新
                is_auto_name_old = isinstance(existing.name, str) and existing.name.startswith("AP_")
                if (not existing.name) or is_auto_name_old:
                    existing.name = name

                # 変更点B: 未設定/other の時のみ補完
                if (not existing.ap_type) or (existing.ap_type == "other"):
                    existing.ap_type = ap_type

                # 他カラムは冪等性のため変更しない
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

        # 変更点C: 新規挿入/既存更新に関わらず、geom が NULL の行は必ず埋める
        db.execute(text(
            "UPDATE access_points "
            "SET geom = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) "
            "WHERE geom IS NULL"
        ))

        db.commit()
        print(f"[load_access_points] upserted={upserted} skipped={skipped}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
