# -*- coding: utf-8 -*-
"""
AccessPoints ローダ（冪等）
- access_points.geojson を読んで UPSERT
"""
import json
from pathlib import Path

from shared.app.database import SessionLocal
from shared.app.models import AccessPoint

AP_GEOJSON = Path("/app/scripts/access_points.geojson")


def main():
    db = SessionLocal()
    try:
        geo = json.loads(AP_GEOJSON.read_text(encoding="utf-8"))
        for f in geo.get("features", []):
            props = f.get("properties", {}) or {}
            geom = f.get("geometry", {}) or {}
            coords = geom.get("coordinates", [])
            if len(coords) != 2:
                continue
            lon, lat = coords
            name = props.get("name") or props.get("title") or "Unnamed"

            existing = (
                db.query(AccessPoint)
                .filter(AccessPoint.name == name, AccessPoint.lat == lat, AccessPoint.lon == lon)
                .one_or_none()
            )
            if existing:
                existing.ap_type = props.get("ap_type", existing.ap_type)
                existing.tags = props.get("tags", existing.tags)
            else:
                db.add(
                    AccessPoint(
                        name=name,
                        lat=lat,
                        lon=lon,
                        ap_type=props.get("ap_type", "unknown"),
                        tags=props.get("tags"),
                    )
                )
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
