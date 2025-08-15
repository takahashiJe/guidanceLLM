# -*- coding: utf-8 -*-
"""
音声認識（STT）ハンドラ
- faster-whisper を CPU で実行（env により compute_type, threads を調整）
- ja/en/zh の自動/明示指定に対応
- 入力: WAV/MP3/OGG いずれもOK（bytes）を一時ファイルに保存して推論
- 出力: テキスト（str）とメタ情報
"""

import os
import io
import tempfile
from typing import Optional, Tuple

from faster_whisper import WhisperModel


class STTHandler:
    def __init__(self) -> None:
        # 環境変数から設定を読み込み（CPU前提）
        self.model_name = os.getenv("STT_MODEL", "base")
        self.device = os.getenv("STT_DEVICE", "cpu")
        self.compute_type = os.getenv("STT_COMPUTE_TYPE", "int8")
        self.max_threads = int(os.getenv("STT_MAX_THREADS", "4"))
        self.lang_auto = os.getenv("STT_LANGUAGE_AUTO", "true").lower() == "true"

        # モデルのロード（CPU・低メモリで安定運用）
        # NOTE: CPU でも base/small 程度なら 1〜2秒台で応答できるケースが多い
        self.model = WhisperModel(
            model_size_or_path=self.model_name,
            device=self.device,
            compute_type=self.compute_type,
            cpu_threads=self.max_threads,
        )

    def transcribe(
        self,
        audio_bytes: bytes,
        lang_hint: Optional[str] = None,
    ) -> Tuple[str, dict]:
        """
        音声（バイト列）→ 文字起こし
        - lang_hint が "ja"|"en"|"zh" の場合は言語固定、それ以外は自動検出
        """
        # 一時ファイルに保存してから読み込む（faster-whisper はパス/ファイルオブジェクトどちらも可）
        with tempfile.NamedTemporaryFile(delete=True, suffix=".wav") as tmp:
            tmp.write(audio_bytes)
            tmp.flush()

            language = None if self.lang_auto and not lang_hint else (lang_hint or None)

            segments, info = self.model.transcribe(
                tmp.name,
                language=language,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=250),
                temperature=0.0,  # 安定性重視
                beam_size=1,
            )

            # 低レイテンシのため逐次結合（セグメント数は短音声なら数個）
            text_parts = []
            for seg in segments:
                text_parts.append(seg.text.strip())

            text = " ".join([t for t in text_parts if t])

            meta = {
                "detected_language": info.language,
                "duration": info.duration,
                "language_probability": getattr(info, "language_probability", None),
            }
            return text.strip(), meta
