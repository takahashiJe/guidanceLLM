# worker/app/services/llm/llm_service.py

from typing import List, Dict, Any, Optional
from langchain_core.messages import BaseMessage

from shared.app.models import Stop, Spot
from worker.app.services.llm.client import OllamaClient
from worker.app.services.llm.prompts import templates, schemas

class LLMInferenceService:
    """
    対話オーケストレーション部からの唯一の窓口として機能するサービスクラス。
    タスクに応じたプロンプトの構築とLLMの実行を担う。
    """

    def __init__(self):
        """サービスの初期化。Ollamaクライアントをインスタンス化する。"""
        self.client = OllamaClient(model="qwen3:30b")
    
    def _get_language_name(self, lang_code: str) -> str:
        """言語コードをプロンプト用の自然言語名に変換する。"""
        if lang_code == "en":
            return "English"
        if lang_code == "zh":
            return "中文"
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

    def generate_nudge_proposal(self, nudge_data: Dict[str, Any], spot_details: Spot) -> Optional[str]:
        """ナッジ提案文を生成する。"""
        lang_name = self._get_language_name(language)
        prompt = templates.NUDGE_PROPOSAL_TEMPLATE.format(
            spot_name=spot_details.official_name_ja,
            social_proof=spot_details.social_proof_ja,
            description=spot_details.description_ja,
            best_date=nudge_data["best_date"],
            weather=nudge_data["weather_on_best_date"]["weather"],
            congestion=nudge_data["congestion_on_best_date"],
            distance_km=nudge_data["distance_km"],
            duration_min=nudge_data["duration_min"],
            language_name=lang_name
        )
        messages = self._prepare_messages(templates.SYSTEM_PROMPT_GUIDE, prompt)
        return self.client.invoke_completion(messages)

    def generate_plan_summary(self, stops: List[Stop]) -> Optional[str]:
        """周遊計画の要約文を生成する。"""
        lang_name = self._get_language_name(language)
        stop_list_str = "\n".join([f"{i+1}. {stop.spot.official_name_ja}" for i, stop in enumerate(stops)])
        prompt = templates.PLAN_SUMMARY_TEMPLATE.format(
            stop_list=stop_list_str,
            language_name=lang_name
        )
        messages = self._prepare_messages(templates.SYSTEM_PROMPT_CONCIERGE, prompt)
        return self.client.invoke_completion(messages)

    def generate_spot_guide_text(self, spot: Spot) -> Optional[str]:
        """ナビゲーション用のスポット案内文を生成する。"""
        lang_name = self._get_language_name(language)
        prompt = templates.SPOT_GUIDE_TEMPLATE.format(
            spot_name=spot.official_name_ja,
            description=spot.description_ja,
            social_proof=spot.social_proof_ja,
            language_name=lang_name
        )
        messages = self._prepare_messages(templates.SYSTEM_PROMPT_TOUR_GUIDE, prompt)
        return self.client.invoke_completion(messages)

    def generate_chitchat_response(self, history: List[BaseMessage]) -> Optional[str]:
        """自然な雑談応答を生成する。"""
        # 雑談では、特別なシステムプロンプトやユーザープロンプトは不要
        messages = self._prepare_messages(templates.SYSTEM_PROMPT_FRIENDLY, "", history=history)
        return self.client.invoke_completion(messages)
        
    def generate_error_message(self) -> Optional[str]:
        """丁寧なエラーメッセージを生成する。"""
        messages = self._prepare_messages(templates.SYSTEM_PROMPT_CONCIERGE, templates.ERROR_MESSAGE_PROMPT)
        return self.client.invoke_completion(messages)

    # --- [達成事項2] 文脈に基づいた自然言語理解 (NLU) ---

    def classify_intent(self, user_input: str, history: List[BaseMessage], app_status: str) -> Optional[Dict[str, Any]]:
        """ユーザーの意図を分類する。"""
        json_schema_str = schemas.IntentClassificationResult.model_json_schema()
        prompt = templates.INTENT_CLASSIFICATION_TEMPLATE.format(
            json_schema=json_schema_str,
            user_input=user_input,
            app_status=app_status
        )
        messages = self._prepare_messages(templates.SYSTEM_PROMPT_ANALYST, prompt, history=history)
        return self.client.invoke_structured_completion(messages, schemas.IntentClassificationResult)

    def extract_plan_edit_parameters(self, user_input: str, stops: List[Stop]) -> Optional[Dict[str, Any]]:
        """計画編集の指示を抽出し、構造化データとして返す。"""
        json_schema_str = schemas.IntentClassificationResult.model_json_schema()
        stop_list_str = "\n".join([f"- {stop.spot.official_name_ja}" for stop in stops])
        prompt = templates.PLAN_EDIT_EXTRACTION_TEMPLATE.format(
            json_schema=json_schema_str,
            user_input=user_input,
            current_plan=stop_list_str
        )
        messages = self._prepare_messages(templates.SYSTEM_PROMPT_ANALYST, prompt)
        return self.client.invoke_structured_completion(messages, schemas.PlanEditParams)