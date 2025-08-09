# worker/app/services/llm/client.py

import ollama
from typing import List, Dict, Any, Optional, Type
from pydantic import BaseModel
import json

class OllamaClient:
    """
    Ollama APIとの通信に特化したクライアントクラス。
    LLMモデルとの直接的な対話をカプセル化し、エラーハンドリングや
    レスポンス形式の制御を行う。
    """

    def __init__(self, model: str = "qwen3:30b", host: Optional[str] = None):
        """
        クライアントを初期化する。

        Args:
            model (str): 使用するOllamaモデル名。
            host (Optional[str]): OllamaサーバーのホストURL。Noneの場合はデフォルト。
        """
        self.model = model
        self.client = ollama.Client(host=host)

    def invoke_completion(self, messages: List[Dict[str, str]]) -> Optional[str]:
        """
        標準的なテキスト生成（Completion）を実行する。

        Args:
            messages (List[Dict[str, str]]): LangChain形式のメッセージ履歴。

        Returns:
            Optional[str]: LLMによって生成されたテキスト。エラー時はNone。
        """
        try:
            response = self.client.chat(
                model=self.model,
                messages=messages
            )
            return response['message']['content']
        except Exception as e:
            print(f"Error invoking Ollama completion: {e}")
            return None

    def invoke_structured_completion(
        self, messages: List[Dict[str, str]], output_schema: Type[BaseModel]
    ) -> Optional[Dict[str, Any]]:
        """
        指定されたPydanticスキーマに従って、構造化されたJSON出力を生成する。

        Args:
            messages (List[Dict[str, str]]): LangChain形式のメッセージ履歴。
            output_schema (Type[BaseModel]): 出力形式を定義したPydanticモデル。

        Returns:
            Optional[Dict[str, Any]]: パースされたJSONデータ。エラー時はNone。
        """
        try:
            # モデルにJSON形式での出力を強制する
            response = self.client.chat(
                model=self.model,
                messages=messages,
                format="json"
            )
            
            # 返ってきたJSON文字列をパースする
            json_response = json.loads(response['message']['content'])
            
            # Pydanticモデルでバリデーション（念のため）
            validated_data = output_schema(**json_response)
            
            return validated_data.dict()
            
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON from Ollama structured response: {e}")
            return None
        except Exception as e:
            print(f"Error invoking Ollama structured completion: {e}")
            return None