# worker/app/services/voice/stt_handler.py

import whisper
import torch
from typing import Optional
import io
import numpy as np
from pydub import AudioSegment, exceptions as pydub_exceptions
import logging

logger = logging.getLogger(__name__)

class STTHandler:
    """
    Whisperモデルに関する全ての処理を担当するクラス。
    """

    def __init__(self, model_name: str = "base"):
        """
        STTHandlerを初期化し、Whisperモデルをメモリにロードする。
        """
        self.model = None
        try:
            logger.info(f"Loading Whisper STT model: {model_name}...")
            device = "cuda" if torch.cuda.is_available() else "cpu"
            self.model = whisper.load_model(model_name, device=device)
            logger.info(f"Whisper STT model '{model_name}' loaded successfully on {device}.")
        except Exception as e:
            logger.error(f"Failed to load Whisper model '{model_name}'. STT will be unavailable. Error: {e}", exc_info=True)

    def transcribe(self, audio_data: bytes) -> Optional[str]:
        """
        音声データを受け取り、文字起こしを実行してテキストを返す。
        """
        if not self.model:
            logger.error("Whisper model is not loaded. Cannot transcribe.")
            return None
        try:
            audio_file = io.BytesIO(audio_data)
            
            # Pydubでの読み込みエラーを捕捉
            try:
                audio = AudioSegment.from_file(audio_file)
            except pydub_exceptions.CouldntDecodeError as e:
                logger.error(f"Failed to decode audio data. It might be corrupted or in an unsupported format. Error: {e}")
                return None

            audio = audio.set_channels(1).set_frame_rate(16000)
            
            samples = np.array(audio.get_array_of_samples()).astype(np.float32) / 32768.0

            result = self.model.transcribe(samples, language="ja", fp16=torch.cuda.is_available())
            
            transcribed_text = result.get("text")
            logger.info(f"Transcription successful. Result: {transcribed_text}")
            return transcribed_text
            
        except Exception as e:
            logger.error(f"Error during audio transcription: {e}", exc_info=True)
            return None

# アプリケーション全体で共有するシングルトンインスタンス
stt_handler_instance = STTHandler()
