# worker/app/services/llm/client.py

import ollama
from typing import List, Dict, Any, Optional, Type
from pydantic import BaseModel, ValidationError
import json
import logging

logger = logging.getLogger(__name__)

class OllamaClient:
    """
    Ollama APIとの通信に特化したクライアントクラス。
    """

    def __init__(self, model: str = "qwen3:30b", host: Optional[str] = None):
        """
        クライアントを初期化する。

        Args:
            model (str): 使用するOllamaモデル名。要件に従い "qwen3:30b" を使用。
            host (Optional[str]): OllamaサーバーのホストURL。Noneの場合はデフォルト。
        """
        self.model = model
        try:
            self.client = ollama.Client(host=host)
            # 起動時に疎通確認を行う
            self.client.list()
            logger.info(f"Ollama client initialized successfully for model '{self.model}'.")
        except Exception as e:
            logger.error(f"Failed to initialize Ollama client. Is the Ollama server running? Error: {e}", exc_info=True)
            # サーバーに接続できない場合は、クライアントをNoneに設定
            self.client = None

    def invoke_completion(self, messages: List[Dict[str, str]]) -> Optional[str]:
        """
        標準的なテキスト生成（Completion）を実行する。
        """
        if not self.client:
            logger.error("Ollama client is not available. Cannot invoke completion.")
            return None
        try:
            response = self.client.chat(
                model=self.model,
                messages=messages
            )
            content = response.get('message', {}).get('content')
            if content is None:
                logger.warning(f"Ollama response did not contain message content: {response}")
                return None
            return content
        except Exception as e:
            logger.error(f"Error invoking Ollama completion: {e}", exc_info=True)
            return None

    def invoke_structured_completion(
        self, messages: List[Dict[str, str]], output_schema: Type[BaseModel]
    ) -> Optional[Dict[str, Any]]:
        """
        指定されたPydanticスキーマに従って、構造化されたJSON出力を生成する。
        """
        if not self.client:
            logger.error("Ollama client is not available. Cannot invoke structured completion.")
            return None
        try:
            response = self.client.chat(
                model=self.model,
                messages=messages,
                format="json"
            )
            content = response.get('message', {}).get('content')
            if not content:
                logger.warning(f"Ollama structured response was empty: {response}")
                return None
            
            json_response = json.loads(content)
            validated_data = output_schema(**json_response)
            
            return validated_data.dict()
            
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON from Ollama. Response: '{content}'. Error: {e}", exc_info=True)
            return None
        except ValidationError as e:
            logger.error(f"Ollama response did not match Pydantic schema. Response: '{content}'. Error: {e}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"Error invoking Ollama structured completion: {e}", exc_info=True)
            return None
