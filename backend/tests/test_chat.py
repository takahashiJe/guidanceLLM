# -*- coding: utf-8 -*-
from io import BytesIO
from fastapi.testclient import TestClient


def test_chat_message_text_accepted(client: TestClient, token_pair):
    _, tokens = token_pair
    at = tokens["access_token"]
    headers = {"Authorization": f"Bearer {at}"}

    # 新規セッションを作っておく
    r = client.post("/api/v1/sessions/create", headers=headers)
    assert r.status_code in (200, 201), r.text
    session_id = r.json()["session_id"]

    # テキストメッセージ受付
    payload = {
        "session_id": session_id,
        "message_text": "元滝伏流水の見どころは？",
        "lang": "ja",
        "input_mode": "text",
    }
    r = client.post("/api/v1/chat/message", json=payload, headers=headers)
    assert r.status_code in (200, 202), r.text
    body = r.json()
    assert body.get("ok", True) is True


def test_chat_message_voice_multipart_accepted(client: TestClient, token_pair):
    _, tokens = token_pair
    at = tokens["access_token"]
    headers = {"Authorization": f"Bearer {at}"}

    # セッション作成
    r = client.post("/api/v1/sessions/create", headers=headers)
    session_id = r.json()["session_id"]

    # ダミーの WAV バイナリ（受付確認用。実音声でなくてもOK）
    empty_wav = BytesIO()
    empty_wav.write(b"RIFF\x24\x00\x00\x00WAVEfmt ")  # 最小ヘッダ風
    empty_wav.seek(0)

    files = {"audio_file": ("dummy.wav", empty_wav, "audio/wav")}
    data = {"session_id": session_id, "lang": "ja", "input_mode": "voice"}

    r = client.post("/api/v1/chat/message", files=files, data=data, headers=headers)
    # STT が走るため 200/202/400 を許容（400 は STT が無音で失敗した場合）
    assert r.status_code in (200, 202, 400), r.text
