from langchain_core.tools import tool
from pydantic.v1 import BaseModel

@tool
def select_route_request_intent() -> str:
    """ユーザーが特定の場所への行き方、所要時間、方向、ナビゲーションを求めている場合に選択する。"""
    return "route_request"

@tool
def select_plan_visit_request_intent() -> str:
    """ユーザーが訪問計画の作成、変更、削除、または混雑状況の確認を求めている場合に選択する。"""
    return "plan_visit_request"

@tool
def select_general_question_intent() -> str:
    """ユーザーがルートや計画以外の、鳥海山の歴史、自然、施設などに関する一般的な質問をしている場合に選択する。"""
    return "general_question"

@tool
def select_greeting_intent() -> str:
    """ユーザーが単純な挨拶や雑談をしている場合に選択する。"""
    return "greeting"

# 分類エージェントが利用するツールのリスト
intent_classification_tools = [
    select_route_request_intent,
    select_plan_visit_request_intent,
    select_general_question_intent,
    select_greeting_intent,
]