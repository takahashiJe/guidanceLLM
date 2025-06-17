# /backend/app/graph/tools.py

import json
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from datetime import date
from typing import Dict, Any

# --- 外部のサービスやRAGモジュールからロジックをインポート ---
from ..services import route_service, planning_service # (planning_serviceを新設)
from ..rag import retriever

# --- 1. 地名正規化ツール ---
class NormalizeNamesInput(BaseModel):
    user_query: str = Field(description="ルート検索に関するユーザーの元の発言。例: '祓川の駐車場から赤滝まで行きたい'")

@tool
def normalize_location_names(input: NormalizeNamesInput) -> Dict[str, str]:
    """ユーザーの曖昧な発言から、正式な出発地と目的地の名称を特定する。
    ルートを計算する前に必ずこのツールを呼び出すこと。"""
    # サービスから既知の地名リストを取得
    known_locations = route_service.get_known_locations()
    
    # このツール内でLLMを呼び出し、正規化を行う
    from .nodes import llm # nodes.pyからLLMインスタンスをインポート
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import JsonOutputParser

    parser = JsonOutputParser()
    prompt = ChatPromptTemplate.from_template(
        """ユーザーの発言を分析し、以下の地名リストの中から最も可能性の高い「出発地」と「目的地」を特定してください。
        もしどちらか一方でも特定できない場合は、該当するフィールドに "不明" と答えてください。
        JSON形式で {"start_point": "地名", "end_point": "地名"} のように回答してください。
        
        地名リスト:
        {location_list}

        ユーザーの発言: "{query}"

        あなたの回答:
        """
        )
    
    chain = prompt | llm | parser
    result = chain.invoke({
        "location_list": "\n- ".join(known_locations),
        "query": input.user_query
    })
    return result


# --- 2. ルート計算ツール (役割を明確化) ---
class CalculateRouteInput(BaseModel):
    start_point: str = Field(description="normalize_location_namesツールで特定された、正式な出発地の名称。")
    end_point: str = Field(description="normalize_location_namesツールで特定された、正式な目的地の名称。")

@tool
def calculate_route(input: CalculateRouteInput) -> Dict[str, Any]:
    """正規化された正式名称を使い、最適なルートを計算する。"""
    route_info = route_service.get_route_from_service(
        start_point=input.start_point,
        end_point=input.end_point
    )
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

# --- 1. ルート計算ツール ---
class CalculateRouteInput(BaseModel):
    start_point: str = Field(description="出発地の名称。例: '祓川駐車場'")
    end_point: str = Field(description="目的地の名称。例: '鳥海山 山頂'")

@tool
def calculate_route(input: CalculateRouteInput) -> Dict[str, Any]:
    """グラフデータとダイクストラ法を使い、出発地から目的地までの最適なルートを計算する。
    このツールは、ルートの提案が必要な場合にのみ使用する。"""
    print(f"--- Tool: calculate_route ---")
    print(f"Input: {input.model_dump_json()}")
    
    # 実際の計算ロジックはroute_serviceに委譲
    route_info = route_service.get_route_from_service(
        start_point=input.start_point,
        end_point=input.end_point
    )
    
    print(f"Output: {route_info}")
    return route_info


# --- 2. 知識検索ツール ---
class KnowledgeSearchInput(BaseModel):
    query: str = Field(description="鳥海山のスポット、コース概要、歴史、文化、自然などに関する一般的な質問に答えるために使用する。")

@tool
def knowledge_base_search(input: KnowledgeSearchInput) -> str:
    """鳥海山に関する専門的な知識ベースから関連情報を検索して返す。"""
    print(f"--- Tool: knowledge_base_search ---")
    print(f"Input: {input.model_dump_json()}")
    
    # 実際のベクトル検索ロジックはrag.retrieverに委譲
    retrieved_text = retriever.query_rag(input.query)
    
    print(f"Output: {retrieved_text[:100]}...") # 長すぎる可能性があるため一部を出力
    return retrieved_text


# --- 3. 訪問計画ツール ---
class PlanVisitInput(BaseModel):
    """訪問計画の確認と登録を行うツールの入力スキーマ"""
    spot_name: str = Field(description="ユーザーが行きたいスポットの名前。")
    visit_date: date = Field(description="ユーザーが行きたい日付。")
    user_id: str = Field(description="操作対象のユーザーID。")

@tool
def check_and_plan_visit(input: PlanVisitInput) -> Dict[str, Any]:
    """指定されたスポットと日付の混雑状況をDBで確認する。
    空いていれば計画を登録し、混雑していれば代替案を提案する。"""
    print(f"--- Tool: check_and_plan_visit ---")
    print(f"Input: {input.model_dump_json()}")

    # 実際のDB操作ロジックはplanning_serviceに委譲
    plan_result = planning_service.process_visit_plan(
        user_id=input.user_id,
        spot_name=input.spot_name,
        visit_date=input.visit_date
    )

    print(f"Output: {plan_result}")
    return plan_result

# --- LangChainエージェントが利用可能なツールのリスト ---
available_tools = [normalize_location_names, calculate_route, knowledge_base_search, check_and_plan_visit, query_knowledge_graph]