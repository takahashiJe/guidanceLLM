# -*- coding: utf-8 -*-
"""
Spots ローダ（冪等）
- POI.json を読んで UPSERT
"""
import json
from pathlib import Path

from shared.app.database import SessionLocal
from shared.app.models import Spot

POI_JSON = Path("/app/worker/app/data/POI.json")


def main():
    db = SessionLocal()
    try:
        data = json.loads(POI_JSON.read_text(encoding="utf-8"))
        for rec in data:
            name = rec.get("official_name")
            if not name:
                continue
            existing = db.query(Spot).filter(Spot.official_name == name).one_or_none()
            if existing:
                existing.lat = rec.get("lat", existing.lat)
                existing.lon = rec.get("lon", existing.lon)
                existing.tags = rec.get("tags", existing.tags)
                existing.spot_type = rec.get("spot_type", existing.spot_type)
                existing.description = rec.get("description", existing.description)
                existing.social_proof = rec.get("social_proof", existing.social_proof)
            else:
                db.add(Spot(**rec))
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
