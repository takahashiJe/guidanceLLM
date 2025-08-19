# -*- coding: utf-8 -*-
from __future__ import annotations

_TRUE = {"1", "true", "yes", "y", "on", "ok", "allow", "allowed", "可", "許可", "可能"}
_FALSE = {"0", "false", "no", "n", "off", "deny", "denied", "不可", "禁止", "無効"}

def _norm(v):
    return str(v).strip().lower()

def _is_truthy(v) -> bool | None:
    s = _norm(v)
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return None  # 不明

def is_car_direct_accessible(spot_type: str | None, tags: dict | None) -> bool:
    """
    ルール優先順位（上から順に適用）:
      1) 明示オーバーライド: tags["car_direct"] が yes/true 等 → True、no/false → False
      2) 種別: 'parking' / 'trailhead' → True、'mountain' → False
      3) アクセス系ヒント: tags["access"] が 'car' 系 → True、'foot'/'trail' 系 → False
      4) それ以外は True（緩めに倒す）
    """
    t = tags or {}

    # 1) 明示オーバーライド
    if "car_direct" in t:
        ov = _is_truthy(t.get("car_direct"))
        if ov is not None:
            return ov

    st = (spot_type or "").lower()

    # 2) 種別ベース
    if st in {"parking", "trailhead"}:
        return True
    if st in {"mountain"}:
        return False

    # 3) アクセス系ヒント
    access = _norm(t.get("access")) if isinstance(t, dict) else ""
    if access in {"car", "drive", "road"}:
        return True
    if access in {"foot", "trail", "hike", "徒歩", "登山"}:
        return False

    # 4) デフォルト
    return True
