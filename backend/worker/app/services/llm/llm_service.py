# worker/app/services/llm/llm_service.py

from typing import List, Dict, Any, Optional
from langchain_core.messages import BaseMessage
import logging

from backend.shared.app.models import Stop, Spot
from backend.worker.app.services.llm.client import OllamaClient
from backend.worker.app.services.llm.prompts import templates, schemas

logger = logging.getLogger(__name__)

class LLMInferenceService:
    """
    対話オーケストレーション部からの唯一の窓口として機能するサービスクラス。
    """

    def __init__(self):
        """サービスの初期化。Ollamaクライアントをインスタンス化する。"""
        self.client = OllamaClient(model="qwen3:30b")
    
    def _get_language_name(self, lang_code: str) -> str:
        """言語コードをプロンプト用の自然言語名に変換する。"""
        if lang_code == "en": return "English"
        if lang_code == "zh": return "中文"
        return "日本語"

    def _prepare_messages(self, system_prompt: str, user_prompt: str, history: Optional[List[BaseMessage]] = None) -> List[Dict[str, str]]:
        """LLMに渡すためのメッセージリストを準備するヘルパーメソッド。"""
        messages = [{"role": "system", "content": system_prompt}]
        if history:
            for msg in history:
                role = "user" if msg.type == "human" else "assistant"
                messages.append({"role": role, "content": str(msg.content)})
        messages.append({"role": "user", "content": user_prompt})
        return messages

    # --- [達成事項1] タスク特化型のテキスト生成 ---

    def generate_nudge_proposal(self, nudge_data: Dict[str, Any], spot_details: Spot, language: str) -> str:
        """ナッジ提案文を生成する。"""
        try:
            lang_name = self._get_language_name(language)
            prompt = templates.NUDGE_PROPOSAL_TEMPLATE.format(
                spot_name=getattr(spot_details, f'official_name_{language}', spot_details.official_name_ja),
                social_proof=getattr(spot_details, f'social_proof_{language}', spot_details.social_proof_ja),
                description=getattr(spot_details, f'description_{language}', spot_details.description_ja),
                best_date=nudge_data.get("best_date", "不明"),
                weather=nudge_data.get("weather_on_best_date", {}).get("weather", "不明"),
                congestion=nudge_data.get("congestion_on_best_date", "不明"),
                distance_km=nudge_data.get("distance_km", "-"),
                duration_min=nudge_data.get("duration_min", "-"),
                language_name=lang_name
            )
            messages = self._prepare_messages(templates.SYSTEM_PROMPT_GUIDE, prompt)
            return self.client.invoke_completion(messages) or "おすすめのスポット情報を作成できませんでした。"
        except Exception as e:
            logger.error(f"Error in generate_nudge_proposal: {e}", exc_info=True)
            return "おすすめ情報の作成中にエラーが発生しました。"

    def generate_plan_summary(self, stops: List[Stop], language: str) -> str:
        """周遊計画の要約文を生成する。"""
        try:
            lang_name = self._get_language_name(language)
            stop_list_str = "\n".join([f"{i+1}. {getattr(stop.spot, f'official_name_{language}', stop.spot.official_name_ja)}" for i, stop in enumerate(stops)])
            prompt = templates.PLAN_SUMMARY_TEMPLATE.format(stop_list=stop_list_str, language_name=lang_name)
            messages = self._prepare_messages(templates.SYSTEM_PROMPT_CONCIERGE, prompt)
            return self.client.invoke_completion(messages) or "計画の要約を作成できませんでした。"
        except Exception as e:
            logger.error(f"Error in generate_plan_summary: {e}", exc_info=True)
            return "計画の要約中にエラーが発生しました。"

    def generate_spot_guide_text(self, spot: Spot, language: str) -> str:
        """ナビゲーション用のスポット案内文を生成する。"""
        try:
            lang_name = self._get_language_name(language)
            prompt = templates.SPOT_GUIDE_TEMPLATE.format(
                spot_name=getattr(spot, f'official_name_{language}', spot.official_name_ja),
                description=getattr(spot, f'description_{language}', spot.description_ja),
                social_proof=getattr(spot, f'social_proof_{language}', spot.social_proof_ja),
                language_name=lang_name
            )
            messages = self._prepare_messages(templates.SYSTEM_PROMPT_TOUR_GUIDE, prompt)
            return self.client.invoke_completion(messages) or "スポット案内を作成できませんでした。"
        except Exception as e:
            logger.error(f"Error in generate_spot_guide_text: {e}", exc_info=True)
            return "スポット案内の作成中にエラーが発生しました。"

    def generate_chitchat_response(self, history: List[BaseMessage], language: str) -> str:
        """自然な雑談応答を生成する。"""
        try:
            lang_name = self._get_language_name(language)
            # 最後のユーザー入力を取得してプロンプトに含める
            user_prompt = ""
            if history and history[-1].type == "human":
                user_prompt = history[-1].content
            
            system_prompt = templates.SYSTEM_PROMPT_FRIENDLY.format(language_name=lang_name)
            messages = self._prepare_messages(system_prompt, user_prompt, history=history[:-1]) #最後の発言は除く
            return self.client.invoke_completion(messages) or "ごめんなさい、うまくお返事できませんでした。"
        except Exception as e:
            logger.error(f"Error in generate_chitchat_response: {e}", exc_info=True)
            return "ごめんなさい、うまくお返事できませんでした。"

    def generate_error_message(self, language: str) -> str:
        """丁寧なエラーメッセージを生成する。"""
        try:
            lang_name = self._get_language_name(language)
            prompt = templates.ERROR_MESSAGE_PROMPT.format(language_name=lang_name)
            messages = self._prepare_messages(templates.SYSTEM_PROMPT_CONCIERGE, prompt)
            return self.client.invoke_completion(messages) or "申し訳ありません、予期せぬエラーが発生しました。"
        except Exception as e:
            logger.error(f"Error in generate_error_message: {e}", exc_info=True)
            return "申し訳ありません、予期せぬエラーが発生しました。"

    # --- [達成事項2] 文脈に基づいた自然言語理解 (NLU) ---

    def classify_intent(self, user_input: str, history: List[BaseMessage], app_status: str, language: str) -> Optional[Dict[str, Any]]:
        """ユーザーの意図を分類する。"""
        try:
            lang_name = self._get_language_name(language)
            json_schema_str = schemas.IntentClassificationResult.model_json_schema()
            prompt = templates.INTENT_CLASSIFICATION_TEMPLATE.format(
                json_schema=json_schema_str,
                user_input=user_input,
                app_status=app_status,
                language_name=lang_name
            )
            messages = self._prepare_messages(templates.SYSTEM_PROMPT_ANALYST, prompt, history=history)
            return self.client.invoke_structured_completion(messages, schemas.IntentClassificationResult)
        except Exception as e:
            logger.error(f"Error in classify_intent: {e}", exc_info=True)
            return None
            
    def extract_plan_edit_parameters(self, user_input: str, stops: List[Stop], language: str) -> Optional[Dict[str, Any]]:
        """計画編集の指示を抽出し、構造化データとして返す。"""
        try:
            lang_name = self._get_language_name(language)
            json_schema_str = schemas.PlanEditParams.model_json_schema()
            stop_list_str = "\n".join([f"- {getattr(stop.spot, f'official_name_{language}', stop.spot.official_name_ja)}" for stop in stops])
            prompt = templates.PLAN_EDIT_EXTRACTION_TEMPLATE.format(
                json_schema=json_schema_str,
                user_input=user_input,
                current_plan=stop_list_str,
                language_name=lang_name
            )
            messages = self._prepare_messages(templates.SYSTEM_PROMPT_ANALYST, prompt)
            return self.client.invoke_structured_completion(messages, schemas.PlanEditParams)
        except Exception as e:
            logger.error(f"Error in extract_plan_edit_parameters: {e}", exc_info=True)
            return None
