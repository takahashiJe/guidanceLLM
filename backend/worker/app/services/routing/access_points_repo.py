# -*- coding: utf-8 -*-
from typing import Optional, Tuple
from sqlalchemy import text
from sqlalchemy.orm import Session

def find_nearest_access_point(
    db: Session, *, lat: float, lon: float, max_km: float | None = None
) -> Optional[tuple[int, str, str, float, float]]:
    """
    駐車場/登山口のうち最寄りを1件返す。max_kmを指定すると、その距離以内のみ許可。
    戻り値: (id, name, ap_type, latitude, longitude) or None
    """
    params = {"lat": lat, "lon": lon}
    where_extra = ""
    if max_km is not None:
        where_extra = "AND ST_DistanceSphere(geom, ST_SetSRID(ST_MakePoint(:lon,:lat),4326)) <= :max_m"
        params["max_m"] = max_km * 1000.0

    sql = text(f"""
        SELECT id, name, ap_type, latitude, longitude
        FROM access_points
        WHERE ap_type IN ('parking','trailhead')
          {where_extra}
        ORDER BY geom <-> ST_SetSRID(ST_MakePoint(:lon,:lat),4326)
        LIMIT 1
    """)
    row = db.execute(sql, params).fetchone()
    if not row:
        return None
    return (row.id, row.name, row.ap_type, row.latitude, row.longitude)
