# /backend/app/graph/tools.py

import json
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from datetime import date
from typing import Dict, Any, Optional, Tuple
from thefuzz import process

# --- 外部のサービスやRAGモジュールからロジックをインポート ---
from app.services import route_service, planning_service
from app.rag import retriever

# ==============================================================================
# --- 1. 地名正規化ツール ---
# ==============================================================================
class NormalizeNamesInput(BaseModel):
    """normalize_location_namesツールへの入力スキーマ。"""
    start: str = Field(description="正規化が必要な出発地の名称。")
    end: str = Field(description="正規化が必要な目的地の名称。")

@tool(args_schema=NormalizeNamesInput)
def normalize_location_names(start: str, end: str) -> Dict[str, str]:
    """
    ユーザーが指定した出発地と目的地の名称を、既知の地名リストと照合し、
    最も可能性の高い正式名称を特定する。「現在地」は特別なキーワードとしてそのまま扱う。
    """
    known_locations = route_service.get_known_locations()
    if not known_locations:
        print("Warning: Known locations list is empty. Returning original names.")
        return {"start_point": start, "end_point": end}

    end_match = process.extractOne(end, known_locations)
    best_end_match = end_match[0] if end_match else end
    
    if start == "現在地":
        best_start_match = "現在地"
    else:
        start_match = process.extractOne(start, known_locations)
        best_start_match = start_match[0] if start_match else start

    result = {"start_point": best_start_match, "end_point": best_end_match}
    print(f"--- Tool: normalize_location_names --- \nInput: start='{start}', end='{end}'\nOutput: {result}")
    return result

# ==============================================================================
# --- 2. ルート計算ツール（再設計） ---
# ==============================================================================
class CalculateRouteInput(BaseModel):
    """calculate_routeツールへの入力スキーマ。"""
    start_point: str = Field(description="正規化された出発地の名称。「現在地」という文字列も含む。")
    end_point: str = Field(description="正規化された目的地の名称。")
    current_location: Optional[Tuple[float, float]] = Field(
        None, description="ユーザーの現在地の緯度・経度。出発地が「現在地」の場合にのみ必須。"
    )

@tool(args_schema=CalculateRouteInput)
def calculate_route(
    start_point: str, 
    end_point: str, 
    current_location: Optional[Tuple[float, float]] = None
) -> Dict[str, Any]:
    """
    正規化された出発地と目的地、およびオプションの現在地座標を受け取り、最適なルートを計算する。
    """
    print(f"--- Tool: calculate_route ---\nInput: start='{start_point}', end='{end_point}', loc={current_location}")
    
    start_coords, end_coords = None, None
    
    if start_point == "現在地":
        if not current_location:
            return {"status": "error", "message": "現在地が不明です。位置情報を有効にして再度お試しください。"}
        if not route_service.is_location_within_haraikawa_area(current_location):
            return {"status": "outside_area", "message": "現在地は道案内サービスの対応エリア外です。お車などで祓川ヒュッテ近辺の駐車場まで移動してください。"}
        start_coords = current_location
    else:
        start_coords = route_service.get_coords_from_location_name(start_point)

    end_coords = route_service.get_coords_from_location_name(end_point)

    if not start_coords or not end_coords:
        return {"status": "error", "message": "出発地または目的地の座標が見つかりませんでした。"}

    print(f"--- Calculating route from {start_coords} to {end_coords} ---")
    route_info = route_service.find_route_from_coords(start_coords=start_coords, end_coords=end_coords)
    return route_info

# Graph RAG のためのツール
# Neo4jなどのグラフDBに接続するのが理想だが、ここでは簡易的にJSONLファイルを読み込む
def load_graph_data():
    graph_data = []
    try:
        # workerコンテナ内のルートにファイルがあると仮定
        with open("./data/graph_data.jsonl", 'r', encoding='utf-8') as f:
            for line in f:
                graph_data.append(json.loads(line))
    except FileNotFoundError:
        print("Knowledge graph file not found. Please run scripts/01_build_knowledge_graph.py first.")
    return graph_data

KNOWLEDGE_GRAPH = load_graph_data()

class GraphSearchInput(BaseModel):
    query: str = Field(description="施設間の関係性や、場所に関する複雑な質問。例: '祓川コースの近くにある温泉付きの宿泊施設は？'")

@tool
def query_knowledge_graph(input: GraphSearchInput) -> str:
    """知識グラフを検索し、エンティティ間の関係性に関する質問に答える。"""
    # この部分は本来、クエリをCypher等に変換し、グラフDBに問い合わせる
    # ここでは簡易的なキーワード検索でシミュレーションする
    keywords = input.query.split() # 簡易的なキーワード抽出
    results = []
    for item in KNOWLEDGE_GRAPH:
        if any(keyword in str(item["triplet"]) for keyword in keywords):
            results.append(str(item["triplet"]))
            
    if not results:
        return "関連する情報が知識グラフに見つかりませんでした。"
    
    return "\n".join(results)

# --- 2. 知識検索ツール ---
class KnowledgeSearchInput(BaseModel):
    query: str = Field(description="鳥海山のスポット、コース概要、歴史、文化、自然などに関する一般的な質問に答えるために使用する。")

@tool(args_schema=KnowledgeSearchInput)
def knowledge_base_search(query: str) -> str:
    """鳥海山に関する専門的な知識ベースから関連情報を検索して返す。"""
    print(f"--- Tool: knowledge_base_search ---\nInput: {query}")
    retrieved_text = retriever.query_rag(query)
    return retrieved_text


# --- 3. 訪問計画ツール ---
class PlanVisitInput(BaseModel):
    spot_name: str = Field(description="ユーザーが行きたいスポットの名前。")
    visit_date: date = Field(description="ユーザーが行きたい日付。")
    user_id: str = Field(description="操作対象のユーザーID。")

@tool(args_schema=PlanVisitInput)
def check_and_plan_visit(user_id: str, spot_name: str, visit_date: date) -> Dict[str, Any]:
    """指定されたスポットと日付の混雑状況を確認し、計画を登録する。"""
    print(f"--- Tool: check_and_plan_visit ---\nInput: user='{user_id}', spot='{spot_name}', date='{visit_date}'")
    plan_result = planning_service.process_visit_plan(user_id=user_id, spot_name=spot_name, visit_date=visit_date)
    return plan_result

# --- LangChainエージェントが利用可能なツールのリスト ---
available_tools = [
    normalize_location_names, 
    calculate_route, 
    knowledge_base_search, 
    check_and_plan_visit, 
    # query_knowledge_graph # このツールはまだ簡易的なので一旦コメントアウトを推奨
]