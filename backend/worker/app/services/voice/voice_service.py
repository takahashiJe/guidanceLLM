# worker/app/services/voice/voice_service.py

from typing import Optional
from worker.app.services.voice.stt_handler import stt_handler_instance
from worker.app.services.voice.tts_handler import tts_handler_instance

class VoiceService:
    """
    音声認識と音声合成の機能を統括するサービスクラス。
    STT/TTSハンドラの具体的な実装を隠蔽し、シンプルなインターフェースを提供する。
    """

    def transcribe_audio_to_text(self, audio_data: bytes) -> Optional[str]:
        """
        [FR-7-1] 音声データをテキストに変換する。
        内部でSTTHandlerを呼び出す。

        Args:
            audio_data (bytes): 音声データのbytes。

        Returns:
            Optional[str]: 変換されたテキスト。
        """
        print("VoiceService: Transcribing audio...")
        return stt_handler_instance.transcribe(audio_data)

    def synthesize_text_to_audio(self, text: str, language: str) -> Optional[bytes]:
        """
        [FR-7-2] テキストを音声データに変換する。
        内部でTTSHandlerを呼び出す。

        Args:
            text (str): 音声に変換するテキスト。
            language (str): テキストの言語コード。

        Returns:
            Optional[bytes]: WAV形式の音声データ。
        """
        print("VoiceService: Synthesizing speech...")
        return tts_handler_instance.synthesize(text, language)
        