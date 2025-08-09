# worker/app/services/llm/prompts/schemas.py

from pydantic import BaseModel, Field
from typing import Literal, Optional, List
from enum import Enum

class Intent(str, Enum):
    """ユーザーの意図を分類するためのカテゴリ"""
    SPECIFIC_SPOT_QUESTION = "specific_spot_question" # 例: 「法体の滝について教えて」
    GENERAL_TOURIST_SPOT_QUESTION = "general_tourist_spot_question" # 例: 「どこか良い観光地ない？」
    CATEGORY_SPOT_QUESTION = "category_spot_question" # 例: 「泊まれるところ探して」
    PLAN_CREATION_REQUEST = "plan_creation_request" # 例: 「旅行の計画を立てたい」
    PLAN_EDIT_REQUEST = "plan_edit_request" # 例: 「計画に〇〇を追加して」
    PLAN_CONFIRMATION = "plan_confirmation" # 例: 「それで確定して」
    PLAN_CANCEL = "plan_cancel" # 例: 「計画をやめる」
    CHITCHAT = "chitchat" # 雑談
    UNKNOWN = "unknown" # 不明

class PlanEditAction(str, Enum):
    """計画編集の操作タイプ"""
    ADD = "add"
    REMOVE = "remove"
    REORDER = "reorder"

class PlanEditPosition(str, Enum):
    """追加/順序変更時の位置指定"""
    BEFORE = "before"
    AFTER = "after"
    FIRST = "first"
    LAST = "last"

class IntentClassificationResult(BaseModel):
    """意図分類タスクの出力スキーマ"""
    intent: Intent = Field(description="分類されたユーザーの意図。")
    extracted_category: Optional[str] = Field(None, description="カテゴリに関する質問の場合、抽出されたカテゴリ名（例：「温泉」、「絶景」）。")

class PlanEditParams(BaseModel):
    """計画編集パラメータ抽出タスクの出力スキーマ"""
    action: PlanEditAction = Field(description="ユーザーが要求している操作（追加、削除、順序変更）。")
    spot_names: List[str] = Field(description="操作対象となるスポット名のリスト。")
    position: Optional[PlanEditPosition] = Field(None, description="追加または順序変更の際の位置指定。")
    target_spot_name: Optional[str] = Field(None, description="位置指定の基準となるスポット名。")