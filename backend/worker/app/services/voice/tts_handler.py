# -*- coding: utf-8 -*-
"""
音声合成（TTS）ハンドラ
- Coqui TTS を CPU で実行
- ja/en/zh の音色/モデルを簡易マッピング
- 出力は WAV（bytes）
"""

import os
import io
from typing import Optional, Tuple

import numpy as np
from TTS.api import TTS
from scipy.io.wavfile import write as wav_write


LANG_MODEL_MAP = {
    # 利用可能なモデルは環境に合わせて変更してください
    # 例の日本語モデル: "tts_models/ja/kokoro/tacotron2-DDC"
    # 英語: "tts_models/en/ljspeech/tacotron2-DDC"
    # 中国語（簡体）例: "tts_models/zh-CN/baker/tacotron2-DDC-GST"
    "ja": os.getenv("TTS_MODEL_JA", "tts_models/ja/kokoro/tacotron2-DDC"),
    "en": os.getenv("TTS_MODEL_EN", "tts_models/en/ljspeech/tacotron2-DDC"),
    "zh": os.getenv("TTS_MODEL_ZH", "tts_models/zh-CN/baker/tacotron2-DDC-GST"),
}


class TTSHandler:
    def __init__(self) -> None:
        self.use_cuda = os.getenv("TTS_USE_CUDA", "0") == "1"
        self.default_voice = os.getenv("TTS_VOICE", "ja-JP")
        self.sample_rate = int(os.getenv("TTS_SAMPLE_RATE", "22050"))

        # モデルは言語ごとにロード（必要時に遅延ロードでも可）
        self._models = {}

    def _get_tts(self, lang: str) -> TTS:
        model_name = LANG_MODEL_MAP.get(lang, LANG_MODEL_MAP["ja"])
        key = f"{lang}:{model_name}"
        if key not in self._models:
            # CPU でロード（cuda=False）
            self._models[key] = TTS(model_name)
        return self._models[key]

    def synthesize(self, text: str, lang: str) -> Tuple[bytes, dict]:
        """
        テキスト→音声（WAV）
        """
        tts = self._get_tts(lang)
        # Coqui は numpy array を返すので WAV にエンコード
        wav: np.ndarray = tts.tts(text=text)
        buf = io.BytesIO()
        wav_write(buf, self.sample_rate, wav.astype(np.float32))
        audio_bytes = buf.getvalue()
        meta = {"sample_rate": self.sample_rate, "lang": lang}
        return audio_bytes, meta
