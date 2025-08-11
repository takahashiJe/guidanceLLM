# worker/app/services/voice/stt_handler.py

import whisper
import torch
from typing import Optional
import io
import numpy as np
from pydub import AudioSegment

class STTHandler:
    """
    Whisperモデルに関する全ての処理を担当するクラス。
    モデルのロードと音声認識（STT）を実行する。
    """

    def __init__(self, model_name: str = "base"):
        """
        STTHandlerを初期化し、Whisperモデルをメモリにロードする。
        この処理はアプリケーション起動時に一度だけ実行されることを想定。

        Args:
            model_name (str): 使用するWhisperモデルの名前 (例: "tiny", "base", "medium")。
        """
        print(f"Loading Whisper STT model: {model_name}...")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = whisper.load_model(model_name, device=device)
        print(f"Whisper STT model '{model_name}' loaded successfully on {device}.")

    def transcribe(self, audio_data: bytes) -> Optional[str]:
        """
        音声データを受け取り、文字起こしを実行してテキストを返す。

        Args:
            audio_data (bytes): WAV, MP3, WebMなど、FFmpegが扱える任意の音声データ。

        Returns:
            Optional[str]: 認識されたテキスト文字列。エラー時はNone。
        """
        try:
            # BytesIOを使ってメモリ上で音声データを扱う
            audio_file = io.BytesIO(audio_data)
            
            # Pydubを使ってWhisperが要求するフォーマットに変換
            audio = AudioSegment.from_file(audio_file)
            audio = audio.set_channels(1).set_frame_rate(16000)
            
            # NumPy配列に変換
            samples = np.array(audio.get_array_of_samples()).astype(np.float32) / 32768.0

            # Whisperで文字起こし
            result = self.model.transcribe(samples, language="ja", fp16=torch.cuda.is_available())
            
            print(f"Transcription successful. Result: {result['text']}")
            return result['text']
            
        except Exception as e:
            print(f"Error during audio transcription: {e}")
            return None

# アプリケーション全体で共有するシングルトンインスタンス
stt_handler_instance = STTHandler()