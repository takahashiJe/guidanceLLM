# -*- coding: utf-8 -*-
"""
NLUタスクの構造化出力をPydanticで定義。
- LLMのJSON出力を厳密にバリデーション
"""

from typing import Optional, Literal
from pydantic import BaseModel, Field, validator


IntentLabel = Literal[
    "general_question",
    "specific_question",
    "plan_creation_request",
    "plan_edit_request",
    "chitchat",
    "other",
]


class IntentClassificationResult(BaseModel):
    """意図分類の検証用モデル"""
    intent: IntentLabel = Field(..., description="分類ラベル")
    confidence: float = Field(..., ge=0.0, le=1.0, description="0.0〜1.0")
    notes: Optional[str] = Field(None, description="短い補足")

    @validator("notes", pre=True)
    def normalize_notes(cls, v):
        # None / 空文字の統一
        if v is None:
            return None
        s = str(v).strip()
        return s or None


PlanAction = Literal["add", "remove", "reorder"]
PlanPosition = Optional[Literal["before", "after", "start", "end"]]


class PlanEditParams(BaseModel):
    """計画編集パラメータ抽出の検証用モデル"""
    action: PlanAction = Field(..., description="編集種別")
    spot_name: Optional[str] = Field(None, description="対象スポット名（add/removeで使用）")
    position: PlanPosition = Field(None, description="挿入/並び替え位置")
    target_spot_name: Optional[str] = Field(
        None, description="基準となる既存スポット名（before/after時）"
    )
    to_index: Optional[int] = Field(
        None, ge=0, description="並べ替えでの移動先インデックス（0始まり）"
    )

    @validator("spot_name", "target_spot_name", pre=True)
    def normalize_str(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        return s or None
