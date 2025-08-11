# backend/scripts/load_spots.py
import json
from backend.shared.app.database import session_scope
from backend.shared.app.models import Spot
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

def load_spots_from_json():
    """
    worker/data/POI.jsonの内容をSpotテーブルに完全にマッピングして投入する。
    多言語対応のネストしたJSON構造を正しく展開する。
    """
    print("Loading spots from POI.json...")
    with session_scope() as db:
        # 既存のデータをクリアする場合（開発時に便利）
        # db.query(Spot).delete()
        
        try:
            with open("backend/worker/data/POI.json", "r", encoding="utf-8") as f:
                spots_data = json.load(f)
        except FileNotFoundError:
            print("Error: backend/worker/data/POI.json not found.")
            return
        except json.JSONDecodeError:
            print("Error: Failed to decode POI.json.")
            return

        for spot_data in spots_data:
            # 座標からgeomを生成
            coords = spot_data.get('coordinates', {})
            latitude = coords.get('latitude')
            longitude = coords.get('longitude')
            
            if latitude is None or longitude is None:
                print(f"Skipping spot_id {spot_data.get('spot_id')} due to missing coordinates.")
                continue
                
            geom = from_shape(Point(longitude, latitude), srid=4326)
            
            # 多言語フィールドを安全に取得するためのヘルパー
            def get_lang_field(field_name, lang_code):
                return spot_data.get(field_name, {}).get(lang_code)

            new_spot = Spot(
                spot_id=spot_data.get('spot_id'),
                # "category"キーを"spot_type"カラムにマッピング
                spot_type=spot_data.get('category'),
                
                # 各言語の公式名称
                official_name_ja=get_lang_field('official_name', 'ja'),
                official_name_en=get_lang_field('official_name', 'en'),
                official_name_zh=get_lang_field('official_name', 'zh'),
                
                # 各言語の説明文
                description_ja=get_lang_field('description', 'ja'),
                description_en=get_lang_field('description', 'en'),
                description_zh=get_lang_field('description', 'zh'),
                
                # 各言語のタグ
                tags_ja=get_lang_field('tags', 'ja'),
                tags_en=get_lang_field('tags', 'en'),
                tags_zh=get_lang_field('tags', 'zh'),
                
                # 各言語の社会的証明（惹句）
                social_proof_ja=get_lang_field('social_proof', 'ja'),
                social_proof_en=get_lang_field('social_proof', 'en'),
                social_proof_zh=get_lang_field('social_proof', 'zh'),
                
                # 緯度・経度とgeom
                latitude=latitude,
                longitude=longitude,
                geom=geom
            )
            db.add(new_spot)
        
        db.commit()
    print(f"Successfully loaded {len(spots_data)} spots.")

if __name__ == "__main__":
    load_spots_from_json()