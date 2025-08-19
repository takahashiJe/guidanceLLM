# -*- coding: utf-8 -*-
## backend/script/load_spots.py
"""
Spots ローダ（冪等実行）
- worker/data/POI.json を読み、spots テーブルへ UPSERT
- 変更点の要旨:
  (1) POI.json の多言語フィールドを、優先順 ja→en→zh で単一列へ正規化
  (2) category→SpotType のマッピングを厳密化（accommodation は facility へ寄せる等）
  (3) アップサート条件を (official_name, latitude, longitude) に統一
  (4) 既存行がある場合、tags は和集合マージ、説明系は欠損時のみ補完
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from shared.app.database import SessionLocal
from shared.app.models import Spot, SpotType  # Spot スキーマに official_name/spot_type/tags/lat/lon 等が定義済み

# === 入力 JSON の既定パス（docker-compose で backend が /app/backend にマウントされる） ===
POI_JSON = Path("/app/backend/worker/data/POI.json")

# --- ユーティリティ ---------------------------------------------------------

LANG_ORDER = ("ja", "en", "zh")  # 多言語 -> 代表値を選ぶ優先順


def pick_lang(d: Optional[Dict[str, Any]]) -> Optional[str]:
    """{ja|en|zh: str} から優先順で1つ選ぶ。"""
    if not d:
        return None
    for k in LANG_ORDER:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    # どれも無ければ最初の非空文字列を返す
    for v in d.values():
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def merge_tags(d: Optional[Dict[str, Any]], extra: Optional[List[str]] = None) -> Optional[List[str]]:
    """
    {ja|en|zh: [str]} を言語横断で set マージし、必要なら extra タグも付ける。
    - 空なら None を返す（DB は NULL 許容）
    """
    merged: Set[str] = set()
    if d:
        for v in d.values():
            if isinstance(v, list):
                for x in v:
                    if isinstance(x, str):
                        s = x.strip()
                        if s:
                            merged.add(s)
    if extra:
        for x in extra:
            if isinstance(x, str):
                s = x.strip()
                if s:
                    merged.add(s)
    if not merged:
        return None
    return sorted(merged)


def map_category_to_spottype(cat: Optional[str]) -> SpotType:
    """POI の 'category' を SpotType へマップ。未知は other。"""
    if not cat:
        return SpotType.other
    c = cat.strip().lower()
    if c == "tourist_spot":
        return SpotType.tourist_spot
    if c == "accommodation":
        # 宿泊系は現行 Enum に専用値が無いため facility へ寄せる
        return SpotType.facility
    if c == "trailhead":
        return SpotType.trailhead
    if c == "parking":
        return SpotType.parking
    # それ以外は other
    return SpotType.other


# --- メイン処理 -------------------------------------------------------------

def upsert_spot(db: Session, item: Dict[str, Any]) -> None:
    """
    1件を UPSERT。
    - 既存判定キー: (official_name, latitude, longitude)
    - 更新ポリシー:
        spot_type: 上書き
        tags: 和集合マージ
        description/social_proof: 既存が欠損(None/空)の場合のみ補完
    """
    coords = item.get("coordinates") or {}
    lat = coords.get("latitude")
    lon = coords.get("longitude")
    if lat is None or lon is None:
        # 座標が無ければスキップ
        return

    # official_name は多言語 → 優先順で代表値に
    name = pick_lang(item.get("official_name"))
    if not name:
        # 名前が取れない場合はスキップ
        return

    spot_type = map_category_to_spottype(item.get("category"))

    # タグは言語横断でマージ。カテゴリもタグとして追加
    tags = merge_tags(item.get("tags"), extra=[str(item.get("category") or "").strip()])

    # 説明系も優先順で選ぶ
    description = pick_lang(item.get("description"))
    social_proof = pick_lang(item.get("social_proof"))

    # 既存行を検索（完全一致）
    existing: Optional[Spot] = (
        db.query(Spot)
        .filter(
            Spot.official_name == name,
            Spot.latitude == float(lat),
            Spot.longitude == float(lon),
        )
        .one_or_none()
    )

    if existing:
        # --- 更新 ---
        # spot_type は上書き
        existing.spot_type = spot_type

        # tags は和集合マージ（既存が JSON 以外や None の場合は上書き）
        if tags:
            if isinstance(existing.tags, list):
                merged = set()
                for t in existing.tags:
                    if isinstance(t, str) and t.strip():
                        merged.add(t.strip())
                for t in tags:
                    if isinstance(t, str) and t.strip():
                        merged.add(t.strip())
                existing.tags = sorted(merged)
            else:
                existing.tags = tags

        # description / social_proof は空の時だけ補完
        if (existing.description is None or not str(existing.description).strip()) and description:
            existing.description = description
        if (existing.social_proof is None or not str(existing.social_proof).strip()) and social_proof:
            existing.social_proof = social_proof

    else:
        # --- 新規挿入 ---
        db.add(
            Spot(
                official_name=name,
                spot_type=spot_type,
                tags=tags,
                latitude=float(lat),
                longitude=float(lon),
                description=description,
                social_proof=social_proof,
            )
        )


def main() -> None:
    db = SessionLocal()
    inserted = updated = skipped = 0
    try:
        data = json.loads(POI_JSON.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("POI.json は配列(JSON list)形式である必要があります。")

        for item in data:
            before = db.query(Spot).count()
            upsert_spot(db, item)
            after = db.query(Spot).count()

            if after > before:
                inserted += 1
            else:
                # 既存更新 or スキップ
                # スキップ判定のため name/lat/lon を確認
                coords = (item or {}).get("coordinates") or {}
                if not coords.get("latitude") or not coords.get("longitude") or not pick_lang(item.get("official_name")):
                    skipped += 1
                else:
                    updated += 1

        db.commit()
        print(f"[load_spots] inserted={inserted} updated={updated} skipped={skipped}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
