# /backend/app/graph/tools.py

import json
from langchain_core.tools import tool
from langchain_core.documents import Document
from pydantic.v1 import BaseModel, Field, validator
from datetime import date, datetime
from typing import Dict, Any, Optional, Tuple
from thefuzz import process
from typing import TypedDict, List, Optional, Literal, Annotated, Tuple

from worker.app.services import route_service, planning_service, planning_spot_service
from worker.app.rag import retriever
from worker.app.db.session import SessionLocal

from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
import os
from dateutil.parser import parse
import traceback

summarize_llm = ChatOllama(
    model="qwen2.5:32b-instruct",
    # model="gemma3:27b-it-qat",
    # model="gemma3:27b",
    # model="llama3:70b",
    # model="elyza-jp-chat",
    base_url=os.getenv("OLLAMA_HOST", "http://ollama:11434"),
    temperature=0 # 要約は創造性より正確性を重視
)

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
    graph_file_path = "app/data/graph_data.jsonl"
    try:
        # workerコンテナ内のルートにファイルがあると仮定
        with open(graph_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                graph_data.append(json.loads(line))
    except FileNotFoundError:
        print("Knowledge graph file not found. Please run scripts/01_build_knowledge_graph.py first.")
    return graph_data

KNOWLEDGE_GRAPH = load_graph_data()

class GraphSearchInput(BaseModel):
    query: str = Field(description="施設間の関係性や、場所に関する複雑な質問。例: '祓川コースの近くにある温泉付きの宿泊施設は？'")

@tool(args_schema=GraphSearchInput)
def query_knowledge_graph(query: str) -> str:
    """
    ユーザーの質問からエンティティを特定し、ナレッジグラフを検索して関連する情報（トリプレット）を返す。
    """
    print(f"--- Tool: query_knowledge_graph ---\nInput: {query}")
    if not KNOWLEDGE_GRAPH:
        return "ナレッジグラフが利用できません。"

    # ユーザーのクエリから既知の場所の名前をすべて抽出する
    known_locations = route_service.get_known_locations()
    found_entities = [loc for loc in known_locations if loc in query]
    
    # もし地名が見つからなければ、より一般的なキーワードで検索を試みる
    if not found_entities:
         # 「温泉」「山小屋」「売店」などのキーワードを抽出
        general_keywords = ["温泉", "山小屋", "売店", "ヒュッテ", "小屋", "駐車場", "トイレ"]
        found_entities.extend([kw for kw in general_keywords if kw in query])

    if not found_entities:
        return "クエリから検索対象となるキーワードが見つかりませんでした。"

    # 見つかったエンティティを含むトリプレットを検索
    results = []
    for entity in found_entities:
        for item in KNOWLEDGE_GRAPH:
            subject, _, obj = item["triplet"]
            if (entity in subject or entity in obj) and item["triplet"] not in results:
                results.append(item["triplet"])

    if not results:
        return "関連する情報がナレッジグラフに見つかりませんでした。"

    # 結果をLLMが解釈しやすい形式の文字列に整形
    formatted_results = [f"主語: {s}, 述語: {p}, 目的語: {o}" for s, p, o in results]
    final_output = "\n".join(formatted_results)
    print(f"--- Tool: query_knowledge_graph ---\nOutput:\n{final_output}")
    return final_output

# ==============================================================================
# --- 3. 知識検索ツール (ベクトル検索) (★★★ 今回の修正の核心 ★★★) ---
# ==============================================================================
class KnowledgeSearchInput(BaseModel):
    query: str = Field(description="鳥海山のスポット、コース概要、歴史、文化、自然などに関する一般的な質問に答えるために使用する。")

@tool(args_schema=KnowledgeSearchInput)
def knowledge_base_search(query: str) -> str:
    """
    鳥海山に関する専門的な知識ベースから関連情報を検索し、その内容を要約して返す。
    """
    print(f"--- Tool: knowledge_base_search ---\nInput: {query}")

    # 1. RAGからDocumentオブジェクトのリストを取得する
    retrieved_docs: List[Document] = retriever.query_rag_and_get_docs(query)
    if not retrieved_docs:
        return "関連する情報がナレッジベースに見つかりませんでした。"

    # 2. 取得したドキュメントの全文を結合する
    full_context = "\n\n---\n\n".join([doc.page_content for doc in retrieved_docs])
    
    # 3. ★★★ LLMを使って結合したテキストを要約する ★★★
    print("--- Summarizing retrieved context... ---")
    
    summarization_prompt = ChatPromptTemplate.from_template(
        """以下の知識ベースの情報を分析し、ユーザーの質問に答えるために必要な情報を、重要なポイントを箇条書きで漏れなく抽出・要約してください。
        
        【ユーザーの質問】
        {user_query}

        【知識ベースの情報】
        {context}

        【要約結果】
        """
    )
    
    summarization_chain = summarization_prompt | summarize_llm | StrOutputParser()
    
    summary = summarization_chain.invoke({
        "user_query": query,
        "context": full_context
    })

    print(f"--- Tool: knowledge_base_search ---\nOutput (Summary):\n{summary}")
    return summary

@tool
def manage_visit_plan(
    user_id: str,
    action: Literal["save", "delete", "check_range"],
    language: str,
    spot_name: Optional[str] = None,
    visit_date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None) -> Dict[str, Any]:
    """
    ユーザーの訪問計画を保存、削除、または指定期間の混雑状況を確認する。
    """
    print(f"--- Tool: manage_visit_plan ---\nInput: user='{user_id}', action='{action}', spot='{spot_name}', date='{visit_date}'")

    parsed_visit_date, parsed_start_date, parsed_end_date = None, None, None
    try:
        if visit_date:
            parsed_visit_date = parse(visit_date).date()
        if start_date:
            parsed_start_date = parse(start_date).date()
        if end_date:
            parsed_end_date = parse(end_date).date()
    except (ValueError, TypeError):
        return {"status": "error", "message": f"日付の形式を認識できませんでした。"}

    db = SessionLocal()
    try:
        target_spot_id = None
        target_spot_name = None
        # アクションが 'save' または 'check_range' の場合、まずスポットを正規化する
        if action in ["save", "check_range"]:
            if not spot_name:
                 return {"status": "error", "message": "場所の名前が指定されていません。"}
            
            # 1. 新しいplanning_spot_serviceを使ってスポットを正規化
            normalized_spot = planning_spot_service.normalize_spot_by_language(spot_name, language)
            
            if not normalized_spot:
                # thefuzzを使ったサジェスト機能はサービス側に集約しても良いが、ツール側でも可能
                return {"status": "invalid_spot", "message": f"「{spot_name}」という場所は見つかりませんでした。"}

            # 2. 正規化された情報を取得
            target_spot_id = normalized_spot["spot_id"]
            target_spot_name = normalized_spot["official_name"][language]
        
        # --- アクションの実行 ---
        result = None
        if action == "save":
            if not all([target_spot_id, target_spot_name, parsed_visit_date]):
                return {"status": "error", "message": "計画の保存には場所と日付の両方が必要です。"}
            # 3. 取得したIDと名前をplanning_serviceに渡す
            result = planning_service.process_plan_creation(db, user_id, target_spot_id, target_spot_name, parsed_visit_date)

        elif action == "delete":
            result = planning_service.process_plan_deletion(db, user_id)

        elif action == "check_range":
            if not all([target_spot_id, target_spot_name, parsed_start_date, parsed_end_date]):
                return {"status": "error", "message": "期間の混雑確認には場所と開始日、終了日が必要です。"}
            # 4. check_rangeでも取得したIDと名前を渡す
            result = planning_service.process_plan_range_check(db, target_spot_id, target_spot_name, parsed_start_date, parsed_end_date)
            
        else:
            result = {"status": "error", "message": f"不明なアクション: {action}"}
        
        # 呼び出し元でコミットされるべきだが、ツール内で完結させるためここでコミット
        if result and result.get("status") in ["saved", "deleted"]:
             db.commit()
        else:
             # エラー時や読み取り専用の場合はロールバック
             db.rollback()

        return result

    except Exception as e:
        db.rollback()
        # traceback情報を文字列として取得
        error_info = traceback.format_exc()
        print("---!!! UNEXPECTED ERROR IN manage_visit_plan TOOL !!!---")
        print(error_info) # ログにも出力試行
        # 最終応答にエラーの全情報を含めて返す
        return {
            "status": "error",
            "message": f"ツール実行中に予期せぬエラーが発生しました。詳細は以下の通りです:\n\n{error_info}"
        }
    finally:
        db.close()

# --- LangChainエージェントが利用可能なツールのリスト ---
available_tools = [
    knowledge_base_search, 
    manage_visit_plan, 
    query_knowledge_graph
]