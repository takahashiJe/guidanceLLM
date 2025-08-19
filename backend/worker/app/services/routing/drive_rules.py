# -*- coding: utf-8 -*-
from __future__ import annotations

def is_car_direct_accessible(spot_type: str | None, tags: dict | None) -> bool:
    """
    とりあえずのルール:
      - 駐車場/登山口系は True
      - 山岳/トレイル系は False
      - tags.access が "car" なら True, "foot"/"trail" なら False
      - それ以外は True に倒す（緩め）
    必要に応じてここを育てる
    """
    st = (spot_type or "").lower()
    if st in {"parking", "trailhead"}:
        return True
    if st in {"mountain"}:
        return False

    access = None
    if isinstance(tags, dict):
        access = (tags.get("access") or "").lower()

    if access in {"car", "drive", "road"}:
        return True
    if access in {"foot", "trail", "hike", "徒歩", "登山"}:
        return False

    return True
