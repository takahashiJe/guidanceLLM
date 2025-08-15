# -*- coding: utf-8 -*-
"""
LLM推論サービス部の公開窓口
- 役割に応じたテンプレートへコンテキストを埋め込み、OllamaClientを呼び出す
- NLUの出力はPydanticで厳密検証（乱れた出力を弾く）
- 多言語一貫性: すべてのテンプレートは lang 指定を含む
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .client import OllamaClient
from .prompts.templates import (
    NUDGE_PROPOSAL_TEMPLATE,
    PLAN_SUMMARY_TEMPLATE,
    SPOT_GUIDE_TEMPLATE,
    ERROR_MESSAGE_TEMPLATE,
    INTENT_CLASSIFICATION_TEMPLATE,
    PLAN_EDIT_EXTRACTION_TEMPLATE,
)
from .prompts.schemas import (
    IntentClassificationResult,
    PlanEditParams,
)


class LLMInferenceService:
    """オーケストレーターから利用される、LLM推論の統一インターフェース"""

    def __init__(
        self,
        client: Optional[OllamaClient] = None,
    ) -> None:
        # DI可能にしてテスト容易性を確保
        self.client = client or OllamaClient()

    # ========= 内部ユーティリティ =========
    @staticmethod
    def _render(template: str, **kwargs) -> str:
        """Pythonのformatではなく、str.replaceベースで簡潔に置換（テンプレ内は {lang} のみを想定）。"""
        out = template
        for k, v in kwargs.items():
            out = out.replace(f"{{{k}}}", str(v))
        return out

    @staticmethod
    def _truncate(text: str, max_chars: int = 2400) -> str:
        """Ollamaへ渡す前に巨大なテキストを切り詰める（安全策）"""
        if len(text) > max_chars:
            return text[:max_chars] + "..."
        return text

    # ========= 1) 生成系 =========
    def generate_nudge_proposal(
        self,
        lang: str,
        spot: Dict[str, Any],
    ) -> str:
        """
        ナッジ提案文を生成
        spot 例:
        {
          "official_name": "...",
          "description": "...",
          "social_proof": "...",
          "distance_km": 12.3,
          "duration_min": 25,
          "best_date": "2025-08-10",
          "weather_on_best_date": "晴れ",
          "congestion_on_best_date": "空いています"
        }
        """
        tpl = self._render(NUDGE_PROPOSAL_TEMPLATE, lang=lang)
        context_lines = [
            f"official_name: {spot.get('official_name')}",
            f"distance/duration: {spot.get('distance_km')} km / {spot.get('duration_min')} min",
            f"best_date: {spot.get('best_date')}",
            f"weather_on_best_date: {spot.get('weather_on_best_date')}",
            f"congestion_on_best_date: {spot.get('congestion_on_best_date')}",
            f"social_proof: {spot.get('social_proof')}",
            "description: ",
            (spot.get("description") or "")[:800],  # 入力安全
        ]
        prompt = tpl + "\n\n" + "\n".join(context_lines)
        prompt = self._truncate(prompt)
        return self.client.invoke_completion(prompt)

    def generate_plan_summary(
        self,
        lang: str,
        stops: List[Dict[str, Any]],
        confirm_question: Optional[str] = None,
    ) -> str:
        """
        訪問順リストの自然文サマリを生成
        stops 例: [{"order": 1, "official_name": "元滝伏流水", "spot_type": "tourist_spot"}, ...]
        """
        tpl = self._render(PLAN_SUMMARY_TEMPLATE, lang=lang)
        stops_lines = []
        for s in stops:
            stops_lines.append(
                f"- #{s.get('order')}: {s.get('official_name')} ({s.get('spot_type')})"
            )
        confirm = confirm_question or {
            "ja": "この内容で確定しますか？",
            "en": "Would you like to confirm this plan?",
            "zh": "要确认这个行程吗？",
        }.get(lang, "Would you like to confirm this plan?")

        prompt = tpl + "\n\n" + "stops:\n" + "\n".join(stops_lines) + f"\n\n確認: {confirm}"
        prompt = self._truncate(prompt)
        return self.client.invoke_completion(prompt)

    def generate_spot_guide_text(
        self,
        lang: str,
        spot: Dict[str, Any],
    ) -> str:
        """
        30秒以内のガイド文を生成（ナビ音声用）
        """
        tpl = self._render(SPOT_GUIDE_TEMPLATE, lang=lang)
        context_lines = [
            f"official_name: {spot.get('official_name')}",
            f"social_proof: {spot.get('social_proof')}",
            "description:",
            (spot.get("description") or "")[:400],
        ]
        prompt = tpl + "\n\n" + "\n".join(context_lines)
        prompt = self._truncate(prompt, max_chars=1600)
        text = self.client.invoke_completion(prompt)
        # 念のため、1段落&最大長を軽く制御
        return " ".join(text.splitlines())[:600]

    def generate_chitchat_response(
        self,
        lang: str,
        recent_history: List[str],
        latest_user_message: str,
    ) -> str:
        """
        雑談応答（簡易）：テンプレートは流用せず、ガード付きの軽量プロンプト
        """
        header = (
            "あなたは親しみやすく、礼儀正しい会話相手です。"
            "事実質問には分からないことは推測せず、一般的な返答に留めます。\n"
        )
        lang_line = f'必ず次の言語で返答してください: "{lang}"。\n'
        history = "\n".join(f"- {h}" for h in recent_history[-5:])
        prompt = f"{header}{lang_line}\n直近履歴:\n{history}\n\nユーザー: {latest_user_message}\n\n回答:"
        prompt = self._truncate(prompt)
        return self.client.invoke_completion(prompt)

    def generate_error_message(
        self,
        lang: str,
        error_context: str,
    ) -> str:
        """
        エラーメッセージ（共感 + 次の行動提案）
        """
        tpl = self._render(ERROR_MESSAGE_TEMPLATE, lang=lang)
        prompt = tpl + "\n\n" + f"error_context: {error_context}"
        prompt = self._truncate(prompt, max_chars=1200)
        return self.client.invoke_completion(prompt)

    # ========= 2) NLU（構造化出力） =========
    def classify_intent(
        self,
        lang: str,
        latest_user_message: str,
        recent_history: List[str],
        app_status: str,
    ) -> IntentClassificationResult:
        """
        意図分類をJSONで返し、Pydanticで検証
        """
        tpl = self._render(INTENT_CLASSIFICATION_TEMPLATE, lang=lang)
        history = "\n".join(f"- {h}" for h in recent_history[-5:])
        ctx = [
            f"latest_user_message: {latest_user_message}",
            f"app_status: {app_status}",
            "recent_history:",
            history,
        ]
        prompt = tpl + "\n\n" + "\n".join(ctx)
        prompt = self._truncate(prompt, max_chars=2000)

        raw = self.client.invoke_structured_completion(prompt)
        # バリデーション（乱れた出力を弾く）
        return IntentClassificationResult.parse_obj(raw)

    def extract_plan_edit_parameters(
        self,
        lang: str,
        user_utterance: str,
        current_stops: List[str],
    ) -> PlanEditParams:
        """
        計画編集パラメータをJSONで返し、Pydanticで検証
        """
        tpl = self._render(PLAN_EDIT_EXTRACTION_TEMPLATE, lang=lang)
        ctx = [
            f"user_utterance: {user_utterance}",
            "current_stops:",
        ] + [f"- {s}" for s in current_stops]
        prompt = tpl + "\n\n" + "\n".join(ctx)
        prompt = self._truncate(prompt, max_chars=2000)

        raw = self.client.invoke_structured_completion(prompt)
        return PlanEditParams.parse_obj(raw)
