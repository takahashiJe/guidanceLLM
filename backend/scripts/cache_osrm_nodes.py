# backend/scripts/cache_osrm_nodes.py
import requests
from backend.shared.app.database import session_scope
from backend.shared.app.models import AccessPoint

def cache_osrm_nodes():
    """AccessPointテーブルの各レコードについて、OSRMの最近傍ノードIDを取得してキャッシュする。"""
    print("Caching OSRM nearest node IDs...")
    
    osrm_urls = {
        "car": "http://localhost:5001/nearest/v1/driving/", # ホストマシンからアクセスする際のポート
        "foot": "http://localhost:5002/nearest/v1/foot/"  # ホストマシンからアクセスする際のポート
    }

    with session_scope() as db:
        access_points = db.query(AccessPoint).all()
        updates = []
        
        for ap in access_points:
            update_data = {'id': ap.id}
            for profile, base_url in osrm_urls.items():
                try:
                    url = f"{base_url}{ap.longitude},{ap.latitude}"
                    response = requests.get(url, timeout=5)
                    response.raise_for_status()
                    data = response.json()
                    
                    if data.get("code") == "Ok" and data.get("waypoints"):
                        node_id = data["waypoints"][0].get("nodes")[0] # OSRM v5.22+
                        update_data[f'{profile}_osrm_node_id'] = node_id
                        print(f"Cached {profile} node for {ap.name or ap.osm_id}")

                except requests.RequestException as e:
                    print(f"Could not cache {profile} node for {ap.osm_id}: {e}")
            
            updates.append(update_data)
        
        if updates:
            db.bulk_update_mappings(AccessPoint, updates)
            db.commit()
    
    print("Finished caching OSRM nodes.")

if __name__ == "__main__":
    cache_osrm_nodes()