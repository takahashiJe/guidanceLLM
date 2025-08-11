# worker/app/services/voice/tts_handler.py

from TTS.api import TTS
import torch
from typing import Optional, Dict
import logging
import os

logger = logging.getLogger(__name__)

class TTSHandler:
    """
    Coqui TTSモデルに関する全ての処理を担当するクラス。
    """

    def __init__(self):
        """
        TTSHandlerを初期化し、各言語に対応するTTSモデルをロードする。
        """
        self.models: Dict[str, TTS] = {}
        self.speaker_wav_path = os.getenv("SPEAKER_WAV_PATH", "default_speaker.wav") # 環境変数から話者音声パスを取得
        
        # 話者音声ファイルが存在するかチェック
        if not os.path.exists(self.speaker_wav_path):
            logger.error(f"Speaker WAV file not found at '{self.speaker_wav_path}'. TTS will likely fail.")
            # ここでダミーファイルを作成するなどのフォールバックも可能

        try:
            logger.info("Loading Coqui TTS models...")
            device = "cuda" if torch.cuda.is_available() else "cpu"
            
            # XTTS-v2は多言語対応なので、一つのモデルを共有する
            model_name = "tts_models/multilingual/multi-dataset/xtts_v2"
            
            logger.info(f"Loading TTS model: {model_name}...")
            # 共通モデルを一度だけロード
            common_model = TTS(model_name).to(device)
            logger.info(f"TTS model '{model_name}' loaded successfully on {device}.")

            # 各言語に共通モデルを割り当て
            for lang in ["ja", "en", "zh"]:
                self.models[lang] = common_model
            
            logger.info("All TTS models assigned.")

        except Exception as e:
            logger.error(f"Failed to load Coqui TTS model. TTS will be unavailable. Error: {e}", exc_info=True)


    def synthesize(self, text: str, language: str) -> Optional[bytes]:
        """
        テキストと言語を受け取り、音声合成を実行してWAV形式のbytesを返す。
        """
        model = self.models.get(language)
        if not model:
            logger.error(f"No TTS model available for language: {language}")
            return None
            
        try:
            # メモリ上でWAV形式のbytesとして直接受け取る
            wav_bytes_list = model.tts(
                text=text,
                speaker_wav=self.speaker_wav_path,
                language=language
            )
            
            # tts()はリストを返すため、bytesに変換
            wav_bytes = bytes(bytearray(wav_bytes_list))

            logger.info(f"Speech synthesis successful for language: {language}")
            return wav_bytes

        except FileNotFoundError:
             logger.error(f"Speaker WAV file not found at '{self.speaker_wav_path}' during synthesis.")
             return None
        except Exception as e:
            logger.error(f"Error during speech synthesis for language '{language}': {e}", exc_info=True)
            return None

# アプリケーション全体で共有するシングルトンインスタンス
tts_handler_instance = TTSHandler()
