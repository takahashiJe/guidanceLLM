# /backend/shared/schemas.py

from pydantic import BaseModel, Field
from typing import TypedDict, Optional, Tuple, Literal, List, Dict, Any
from datetime import date

class ActionPayload(TypedDict, total=False):
    """フロントエンドへのアクション指示のスキーマ"""
    type: Literal["draw_route", "highlight_spot"]
    payload: dict

class VisitPlanResponse(BaseModel):
    """
    フロントエンドに返す訪問計画情報のスキーマ
    """
    spot_name: str = Field(..., description="計画されている場所の名前")
    visit_date: date = Field(..., description="計画されている日付")

class ChatRequest(BaseModel):
    """
    /chatエンドポイントへのリクエストボディのスキーマ
    """
    user_id: str = Field(..., description="ユーザーを識別するためのID")
    message: str = Field(..., description="ユーザーからの新しいメッセージ")

    language: Literal["ja", "en", "zh", "other"] = Field(
        ..., description="ユーザーが選択した言語コード"
    )
    
    # フロントエンドが保持している現在の対話状態をバックエンドに伝える
    task_status: Literal["idle", "confirming_route", "guiding"] = Field(
        "idle", description="現在の対話タスクの状態"
    )
    
    # オプショナルな追加情報
    current_location: Optional[Tuple[float, float]] = Field(None, description="ユーザーの現在地の緯度経度")
    user_profile: Optional[Dict[str, Any]] = Field(None, description="ユーザーのスキルレベルなどのプロフィール")


class ChatResponse(BaseModel):
    """
    /chatエンドポイントのレスポンスボディのスキーマ
    """
    answer_text: str = Field(..., description="AIからの応答メッセージ")
    
    # LangGraphが更新した新しい対話状態をフロントエンドに返す
    task_status: Literal["idle", "confirming_route", "guiding"] = Field(
        ..., description="更新された対話タスクの状態"
    )
    
    # フロントエンドで描画などのアクションが必要な場合にデータを渡す
    action: Optional[ActionPayload] = Field(None, description="フロントエンドへの描画指示などのアクション")

class UpdateLocationRequest(BaseModel):
    """
    /update_locationエンドポイントへのリクエストボディのスキーマ
    """
    user_id: str = Field(..., description="ユーザーを識別するためのID")
    current_location: Tuple[float, float] = Field(..., description="ユーザーの現在地の緯度経度")
    
    # 案内中かどうかを判断するためにtask_statusも受け取る
    task_status: Literal["idle", "confirming_route", "guiding"] = Field(
        ..., description="現在の対話タスクの状態"
    )

class UpdateLocationResponse(BaseModel):
    """
    /update_locationエンドポイントのレスポンスボディのスキーマ
    """
    # AIからの介入が必要な場合にメッセージを返す
    intervention_message: Optional[str] = Field(None, description="ルート逸脱時などの介入メッセージ")
    # 案内が終了した場合などにステータスを更新する
    new_task_status: Optional[Literal["idle", "confirming_route", "guiding"]] = Field(
        None, description="更新された対話タスクの状態"
    )