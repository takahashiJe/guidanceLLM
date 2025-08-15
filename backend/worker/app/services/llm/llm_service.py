# -*- coding: utf-8 -*-
"""
LLM 推論サービス（長期記憶プロンプト対応版）
- 役割:
  - 各種テンプレートを用いて qwen3:30b へ指示
  - JSON 構造化の検証（Pydantic）はフェーズ3で実装済み
  - ★ 長期記憶（long_term_context）をテンプレートに注入（任意）

注: ここでは prompts/templates.py の各 TEMPLATE に
     {long_term_context} を追加済みであることが前提。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from worker.app.services.llm.client import OllamaClient
from worker.app.services.llm.prompts import templates
from worker.app.services.llm.prompts.schemas import (
    IntentClassificationResult,
    PlanEditParams,
)


class LLMInferenceService:
    def __init__(self, model_name: Optional[str] = None, default_lang: str = "ja"):
        self.client = OllamaClient(model_name=model_name)
        self.default_lang = default_lang

    # -------------------------------
    # 生成系
    # -------------------------------
    def generate_nudge_proposal(
        self,
        *,
        lang: str,
        user_message: str,
        nudge_materials: Dict[str, Any],
        long_term_context: str = "",  # ★ 追加
    ) -> str:
        prompt = templates.NUDGE_PROPOSAL_TEMPLATE.format(
            lang=lang,
            user_message=user_message,
            nudge_materials=nudge_materials,
            long_term_context=long_term_context,  # ★ 追加
        )
        return self.client.invoke_completion(prompt, lang=lang)

    def generate_plan_summary(
        self,
        *,
        lang: str,
        stops: List[Dict[str, Any]],
        long_term_context: str = "",  # 任意（計画の個人文脈が必要なら利用）
    ) -> str:
        prompt = templates.PLAN_SUMMARY_TEMPLATE.format(
            lang=lang,
            stops=stops,
            long_term_context=long_term_context,
        )
        return self.client.invoke_completion(prompt, lang=lang)

    def generate_spot_guide_text(
        self,
        *,
        lang: str,
        spot: Dict[str, Any],
        long_term_context: str = "",
    ) -> str:
        prompt = templates.SPOT_GUIDE_TEMPLATE.format(
            lang=lang,
            spot=spot,
            long_term_context=long_term_context,
        )
        return self.client.invoke_completion(prompt, lang=lang)

    def generate_chitchat_response(
        self,
        *,
        lang: str,
        chat_history: List[Dict[str, Any]],
        user_message: str,
        long_term_context: str = "",  # ★ 追加
    ) -> str:
        prompt = templates.CHITCHAT_TEMPLATE.format(
            lang=lang,
            chat_history=chat_history,
            user_message=user_message,
            long_term_context=long_term_context,  # ★ 追加
        )
        return self.client.invoke_completion(prompt, lang=lang)

    def generate_error_message(
        self,
        *,
        lang: str,
        error_context: str,
        long_term_context: str = "",
    ) -> str:
        prompt = templates.ERROR_MESSAGE_TEMPLATE.format(
            lang=lang,
            error_context=error_context,
            long_term_context=long_term_context,
        )
        return self.client.invoke_completion(prompt, lang=lang)

    # -------------------------------
    # NLU 系
    # -------------------------------
    def classify_intent(
        self,
        *,
        lang: str,
        latest_user_message: str,
        app_status: str,
        chat_history: List[Dict[str, Any]],
        long_term_context: str = "",  # 任意
    ) -> IntentClassificationResult:
        prompt = templates.INTENT_CLASSIFICATION_TEMPLATE.format(
            lang=lang,
            latest_user_message=latest_user_message,
            app_status=app_status,
            chat_history=chat_history,
            long_term_context=long_term_context,
        )
        data = self.client.invoke_structured_completion(
            prompt, pydantic_model=IntentClassificationResult
        )
        return data

    def extract_plan_edit_parameters(
        self,
        *,
        lang: str,
        user_message: str,
        current_stops: List[Dict[str, Any]],
        long_term_context: str = "",
    ) -> PlanEditParams:
        prompt = templates.PLAN_EDIT_EXTRACTION_TEMPLATE.format(
            lang=lang,
            user_message=user_message,
            current_stops=current_stops,
            long_term_context=long_term_context,
        )
        data = self.client.invoke_structured_completion(
            prompt, pydantic_model=PlanEditParams
        )
        return data
