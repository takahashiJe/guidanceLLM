# worker/app/services/voice/tts_handler.py

from TTS.api import TTS
import torch
from typing import Optional, Dict

class TTSHandler:
    """
    Coqui TTSモデルに関する全ての処理を担当するクラス。
    多言語モデルの管理と音声合成（TTS）を実行する。
    """

    def __init__(self):
        """
        TTSHandlerを初期化し、各言語に対応するTTSモデルをロードする。
        """
        print("Loading Coqui TTS models...")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        
        # 使用するモデル名を辞書で管理
        # 注意: これらのモデル名は例です。環境に合わせて適切なモデルを指定してください。
        # https://huggingface.co/coqui
        model_names = {
            "ja": "coqui/XTTS-v2", # 日本語を含む多言語モデル
            "en": "coqui/XTTS-v2", # 英語
            "zh": "coqui/XTTS-v2"  # 中国語
        }
        
        self.models: Dict[str, TTS] = {}
        for lang, model_name in model_names.items():
            # 既にロード済みの多言語モデルは共有する
            if model_name in [m.model_name for m in self.models.values()]:
                # find existing model
                for loaded_model in self.models.values():
                    if loaded_model.model_name == model_name:
                        self.models[lang] = loaded_model
                        break
            else:
                 print(f"Loading TTS model for '{lang}': {model_name}...")
                 self.models[lang] = TTS(model_name).to(device)
                 print(f"TTS model for '{lang}' loaded successfully.")
        
        print("All TTS models loaded.")

    def synthesize(self, text: str, language: str) -> Optional[bytes]:
        """
        テキストと言語を受け取り、音声合成を実行してWAV形式のbytesを返す。

        Args:
            text (str): 音声に変換するテキスト。
            language (str): テキストの言語コード ('ja', 'en', 'zh')。

        Returns:
            Optional[bytes]: WAV形式の音声データ。エラー時はNone。
        """
        model = self.models.get(language)
        if not model:
            print(f"No TTS model found for language: {language}")
            return None
            
        try:
            # XTTS-v2はspeaker_wavで話者を指定する必要がある
            # ここではサンプルとして用意した音声ファイルパスを指定
            # TODO: 各言語に最適な話者の音声ファイルを用意する
            speaker_wav_path = "path/to/your/speaker_voice.wav" # このパスは環境に合わせて変更してください

            # wav形式でメモリ上に出力
            wav_bytes = model.tts(
                text=text,
                speaker_wav=speaker_wav_path,
                language=language
            )
            print(f"Speech synthesis successful for language: {language}")
            return bytes(wav_bytes)

        except Exception as e:
            print(f"Error during speech synthesis: {e}")
            return None

# アプリケーション全体で共有するシングルトンインスタンス
tts_handler_instance = TTSHandler()