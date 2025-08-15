# backend/worker/app/tasks.py
# -*- coding: utf-8 -*-
# ------------------------------------------------------------
# 役割:
#  - API Gateway から Celery 経由で渡されるタスクを受け取り、
#    各専門サービス（オーケストレーション、音声、情報、経路など）
#    に委譲する「受け口」を集約する。
#  - タスク名は shared 側フォワーダ（send_task の宛先名）と 1:1 で一致させる。
#  - ここではビジネスロジックは極力書かず、呼び出しと例外処理に徹する。
#
# ポイント:
#  - テキスト/音声の混在に対応（音声は STT でテキスト化）
#  - LangGraph の実行（orchestrator）を単一のタスクに集約
#  - ナビ開始/位置更新のトリガーを用意（Navigation Service 連携）
#  - 事前ガイド生成（pre_generated_guides へ保存はサービス側の責務）
# ------------------------------------------------------------

from __future__ import annotations

import base64
import traceback
from typing import Any, Dict, Optional, Tuple

from shared.app.celery_app import celery_app
from shared.app.database import SessionLocal  # 将来 DB トランザクションが必要な場合に使用
from shared.app import models, schemas        # 型定義やモデル（必要に応じて利用）
from shared.app.schemas import (
    STTRequest,
    STTResult,
    TTSRequest,
    TTSResult,
)

# Worker 側サービス群
from worker.app.services.voice.voice_service import VoiceService
from worker.app.services.orchestration import state as orch_state
from worker.app.services.orchestration.graph import build_graph  # LangGraph 構築
from worker.app.services.navigation.navigation_service import NavigationService
from worker.app.services.information.information_service import InformationService    # noqa: F401
from worker.app.services.itinerary.itinerary_service import ItineraryService          # noqa: F401
from worker.app.services.routing.routing_service import RoutingService                # noqa: F401

# ============================================================
# Celery タスク名（API 側フォワーダと一致させること）
# ============================================================
TASK_ORCHESTRATE_CONVERSATION = "worker.app.tasks.orchestrate_message"
TASK_START_NAVIGATION = "worker.app.tasks.navigation_start"
TASK_UPDATE_LOCATION = "worker.app.tasks.navigation_location_update"
TASK_PREGENERATE_GUIDES = "worker.app.tasks.pregenerate_guides"

# （音声系タスクは既存の運用に合わせて namespaced）
TASK_STT = "voice.stt_transcribe"
TASK_TTS = "voice.tts_synthesize"


# ============================================================
# ユーティリティ
# ============================================================
def _ensure_text_from_audio_if_needed(
    *,
    message_text: Optional[str],
    audio_b64: Optional[str],
    source_lang: Optional[str],
) -> str:
    """
    入力が音声のみの場合は STT でテキスト化し、テキストがあればそれを優先する。
    Whisper の言語指定はヒント（None なら自動検出）。
    """
    if message_text and message_text.strip():
        return message_text.strip()

    if audio_b64:
        try:
            audio_bytes = base64.b64decode(audio_b64)
        except Exception:
            raise ValueError("音声データ(base64)のデコードに失敗しました。")

        service = _get_voice_service()
        text, meta = service.transcribe(audio_bytes, lang_hint=source_lang)
        if not text or not text.strip():
            raise ValueError("音声のテキスト化に失敗しました。")
        return text.strip()

    raise ValueError("message_text も audio_b64 も指定されていません。")


_voice_service: Optional[VoiceService] = None


def _get_voice_service() -> VoiceService:
    """
    VoiceService は重い初期化を行う可能性があるため、シングルトン運用。
    """
    global _voice_service
    if _voice_service is None:
        _voice_service = VoiceService()
    return _voice_service


# ============================================================
# オーケストレーション（LangGraph）エントリ
# ============================================================
@celery_app.task(name=TASK_ORCHESTRATE_CONVERSATION, bind=True)
def orchestrate_message(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    対話の全体オーケストレーションを実行する中核タスク。
    入力:
      {
        "session_id": str,         # 必須
        "user_id": int | null,
        "lang": "ja"|"en"|"zh",    # 省略時 ja
        "input_mode": "text"|"voice",
        "message_text": str | null,
        "audio_b64": str | null
      }
    戻り値（最小限のサマリ。詳細は DB から UI 側が復元）:
      {
        "ok": bool,
        "session_id": str,
        "app_status": str | null,
        "active_plan_id": int | null,
        "final_response": str | null,
        "error": str | null
      }
    """
    try:
        session_id: str = payload.get("session_id")
        if not session_id:
            raise ValueError("session_id は必須です。")

        user_id: Optional[int] = payload.get("user_id")
        lang: str = payload.get("lang", "ja")
        input_mode: str = payload.get("input_mode", "text")
        message_text: Optional[str] = payload.get("message_text")
        audio_b64: Optional[str] = payload.get("audio_b64")

        # 1) 必要なら STT を実行してテキスト化
        latest_text = _ensure_text_from_audio_if_needed(
            message_text=message_text, audio_b64=audio_b64, source_lang=lang
        )

        # 2) LangGraph を構築
        app = build_graph()

        # 3) 既存状態をロード
        agent_state = orch_state.load_agent_state(session_id=session_id)

        # 4) グラフ実行
        result_state = app.invoke(
            {
                "session_id": session_id,
                "user_id": user_id,
                "lang": lang,
                "input_mode": input_mode,
                "latest_user_message": latest_text,
                "agent_state": agent_state,
            }
        )

        # 5) 状態を永続化
        orch_state.save_agent_state(session_id=session_id, agent_state=result_state)

        return {
            "ok": True,
            "session_id": session_id,
            "app_status": result_state.get("app_status"),
            "active_plan_id": result_state.get("active_plan_id"),
            "final_response": result_state.get("final_response"),
        }

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}


# ============================================================
# ナビゲーション系（開始・位置更新・ガイド事前生成）
# ============================================================
@celery_app.task(name=TASK_START_NAVIGATION, bind=True)
def navigation_start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    ナビゲーション開始のトリガー。
    - nodes / services 側でガイド文の事前生成を行い、
      pre_generated_guides へ保存する想定（ここでは委譲）。
    """
    try:
        session_id: str = payload.get("session_id")
        if not session_id:
            raise ValueError("session_id は必須です。")

        user_id: Optional[int] = payload.get("user_id")
        lang: str = payload.get("lang", "ja")

        app = build_graph()
        agent_state = orch_state.load_agent_state(session_id=session_id)

        result_state = app.invoke(
            {
                "session_id": session_id,
                "user_id": user_id,
                "lang": lang,
                "latest_user_message": "[SYSTEM_TRIGGER:NAVIGATION_START]",
                "agent_state": agent_state,
                "force_navigation_start": True,
            }
        )

        orch_state.save_agent_state(session_id=session_id, agent_state=result_state)

        return {
            "ok": True,
            "session_id": session_id,
            "app_status": result_state.get("app_status"),
        }
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}


@celery_app.task(name=TASK_UPDATE_LOCATION, bind=True)
def navigation_location_update(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    位置情報の継続アップデート（ナビ実行中）。
    NavigationService に委譲し、逸脱/接近の検知→必要に応じて
    オーケストレーションやリルート計算へ通知する。
    """
    try:
        session_id: str = payload.get("session_id")
        if not session_id:
            raise ValueError("session_id は必須です。")

        lat = payload.get("lat")
        lon = payload.get("lon")
        if lat is None or lon is None:
            raise ValueError("lat, lon は必須です。")

        nav = NavigationService()
        events = nav.process_tick(
            session_id=session_id,
            current_location={"lat": float(lat), "lon": float(lon)},
        )
        # events 例: [{"type":"deviation_detected"}, {"type":"proximity", "spot_id":"..."}]

        return {"ok": True, "session_id": session_id, "events": events}

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}


@celery_app.task(name=TASK_PREGENERATE_GUIDES, bind=True)
def pregenerate_guides(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    ガイド文を事前生成して保存するタスク。
    実体は Orchestrator/LLM/Information の連携で生成し、DB (pre_generated_guides) に保存。
    """
    try:
        session_id: str = payload.get("session_id")
        if not session_id:
            raise ValueError("session_id は必須です。")

        lang: str = payload.get("lang", "ja")

        app = build_graph()
        agent_state = orch_state.load_agent_state(session_id=session_id)

        result_state = app.invoke(
            {
                "session_id": session_id,
                "lang": lang,
                "latest_user_message": "[SYSTEM_TRIGGER:PREGENERATE_GUIDES]",
                "agent_state": agent_state,
                "force_pregenerate_guides": True,
            }
        )

        orch_state.save_agent_state(session_id=session_id, agent_state=result_state)

        return {"ok": True, "session_id": session_id}

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}


# ============================================================
# STT / TTS（音声サービス直参照）
# ============================================================
@celery_app.task(name=TASK_STT, bind=True)
def stt_transcribe(self, payload: dict) -> dict:
    """
    base64 音声 → テキスト
    """
    try:
        req = STTRequest(**payload)
        service = _get_voice_service()
        audio_bytes = base64.b64decode(req.audio_b64)
        text, meta = service.transcribe(audio_bytes, lang_hint=req.lang)
        return STTResult(
            text=text,
            detected_language=meta.get("detected_language"),
            duration=meta.get("duration"),
            language_probability=meta.get("language_probability"),
        ).model_dump()
    except Exception as e:
        traceback.print_exc()
        return STTResult(text="", detected_language=None, duration=None, language_probability=None).model_dump()


@celery_app.task(name=TASK_TTS, bind=True)
def tts_synthesize(self, payload: dict) -> dict:
    """
    テキスト → base64 WAV
    """
    try:
        req = TTSRequest(**payload)
        service = _get_voice_service()
        audio_bytes, meta = service.synthesize(req.text, lang=req.lang)
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        return TTSResult(
            audio_b64=audio_b64,
            sample_rate=meta.get("sample_rate", 22050),
            lang=req.lang,
        ).model_dump()
    except Exception:
        traceback.print_exc()
        # 失敗時は無音1フレームを返す等のフォールバックも検討可
        return TTSResult(audio_b64="", sample_rate=22050, lang=payload.get("lang", "ja")).model_dump()
