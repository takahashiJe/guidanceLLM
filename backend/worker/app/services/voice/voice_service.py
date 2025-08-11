# worker/app/services/voice/voice_service.py

from typing import Optional
import logging
from backend.worker.app.services.voice.stt_handler import stt_handler_instance
from backend.worker.app.services.voice.tts_handler import tts_handler_instance

logger = logging.getLogger(__name__)

class VoiceService:
    """
    音声認識と音声合成の機能を統括するサービスクラス。
    """

    def transcribe_audio_to_text(self, audio_data: bytes) -> Optional[str]:
        """
        [FR-7-1] 音声データをテキストに変換する。
        """
        logger.info("VoiceService: Transcribing audio...")
        if not audio_data:
            logger.warning("transcribe_audio_to_text received empty audio data.")
            return None
        try:
            return stt_handler_instance.transcribe(audio_data)
        except Exception as e:
            # STTHandler内で捕捉しきれなかった予期せぬエラーに対応
            logger.error(f"An unexpected error occurred in transcribe_audio_to_text: {e}", exc_info=True)
            return None

    def synthesize_text_to_audio(self, text: str, language: str) -> Optional[bytes]:
        """
        [FR-7-2] テキストを音声データに変換する。
        """
        logger.info(f"VoiceService: Synthesizing speech for language '{language}'...")
        if not text:
            logger.warning("synthesize_text_to_audio received empty text.")
            return None
        try:
            return tts_handler_instance.synthesize(text, language)
        except Exception as e:
            logger.error(f"An unexpected error occurred in synthesize_text_to_audio: {e}", exc_info=True)
            return None
