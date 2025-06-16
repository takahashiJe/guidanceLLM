# /backend/app/graph/tools.py

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from datetime import date

# 本来は外部のサービスやRAGモジュールからロジックをインポートします
# from app.services.route_service import get_route_from_service
# from app.rag.retriever import get_info_from_rag

# --- ルート計算ツールの入力スキーマ ---
class CalculateRouteInput(BaseModel):
    start_point: str = Field(description="出発地の名称。例: '祓川駐車場'")
    end_point: str = Field(description="目的地の名称。例: '鳥海山 山頂'")

@tool
def calculate_route(input: CalculateRouteInput) -> dict:
    """グラフデータとダイクストラ法を使い、出発地から目的地までの最適なルートを計算する。"""
    # route_info = get_route_from_service(input.start_point, input.end_point)
    # return route_info
    pass

# --- 知識検索ツールの入力スキーマ ---
class KnowledgeSearchInput(BaseModel):
    query: str = Field(description="鳥海山のスポット、コース、歴史などに関する質問。")

@tool
def knowledge_base_search(input: KnowledgeSearchInput) -> str:
    """鳥海山の知識ベースから関連情報を検索する。"""
    # retrieved_text = get_info_from_rag(input.query)
    # return retrieved_text
    pass

class PlanVisitInput(BaseModel):
    """訪問計画の確認と登録を行うツールの入力スキーマ"""
    spot_name: str = Field(description="ユーザーが行きたいスポットの名前。")
    visit_date: date = Field(description="ユーザーが行きたい日付。")
    user_id: str = Field(description="操作対象のユーザーID。")

@tool
def check_and_plan_visit(input: PlanVisitInput) -> dict:
    """指定されたスポットと日付の混雑状況をDBで確認する。
    空いていれば計画を登録し、混雑していれば代替案を提案する。"""
    # 戻り値例1 (成功): {"status": "available", "message": "訪問計画を登録しました。"}
    # 戻り値例2 (混雑): {"status": "congested", "suggestion": "翌日のYYYY-MM-DDはいかがですか？"}
    pass

# グラフで使用するツールのリスト
available_tools = [calculate_route, knowledge_base_search, check_and_plan_visit]