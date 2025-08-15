# -*- coding: utf-8 -*-
"""
VoiceService
- STT/TTS をまとめる薄いファサード
- lang は "ja"|"en"|"zh" 想定
"""

from typing import Optional, Tuple

from .stt_handler import STTHandler
from .tts_handler import TTSHandler


class VoiceService:
    def __init__(self) -> None:
        self.stt = STTHandler()
        self.tts = TTSHandler()

    # -------- STT --------
    def transcribe(self, audio_bytes: bytes, lang_hint: Optional[str] = None) -> Tuple[str, dict]:
        return self.stt.transcribe(audio_bytes, lang_hint=lang_hint)

    # -------- TTS --------
    def synthesize(self, text: str, lang: str) -> Tuple[bytes, dict]:
        return self.tts.synthesize(text, lang=lang)
