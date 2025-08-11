# backend/scripts/load_access_points.py
import geojson
from backend.shared.app.database import session_scope
from backend.shared.app.models import AccessPoint
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

def load_access_points_from_geojson(filepath: str):
    """Overpass APIから抽出したGeoJSONをAccessPointテーブルに投入する。"""
    print(f"Loading access points from {filepath}...")
    with session_scope() as db:
        db.query(AccessPoint).delete()
        
        with open(filepath, "r", encoding="utf-8") as f:
            data = geojson.load(f)
            
            for feature in data['features']:
                props = feature['properties']
                coords = feature['geometry']['coordinates']
                
                access_type = props.get('amenity') or props.get('highway')
                if not access_type:
                    continue
                    
                geom = from_shape(Point(coords[0], coords[1]), srid=4326)
                
                new_ap = AccessPoint(
                    osm_id=str(feature['id']),
                    access_type=access_type,
                    name=props.get('name'),
                    tags=props,
                    latitude=coords[1],
                    longitude=coords[0],
                    geom=geom
                )
                db.add(new_ap)
        
        db.commit()
    print("Successfully loaded access points.")

if __name__ == "__main__":
    # ダウンロードしたGeoJSONファイルへのパスを指定
    load_access_points_from_geojson("backend/scripts/access_points.geojson")