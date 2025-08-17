# backend/worker/app/tasks.py
# ------------------------------------------------------------
# 役割:
#  - API Gateway から Celery 経由で渡されるタスクを受け取り、
#    各専門サービス（オーケストレーション、音声、情報、経路など）
#    に委譲する「受け口」を集約する。
#  - タスク名は必ず shared.app.tasks のシグネチャ（名前）と一致させる。
#  - ここではビジネスロジックは極力書かず、呼び出しと例外処理に徹する。
#
# ポイント:
#  - テキスト/音声の混在に対応（音声はSTTでテキスト化）
#  - LangGraph の実行（orchestrator）を単一のタスクに集約
#  - ナビ開始/位置更新のトリガーを用意（Navigation Service連携）
#  - 事前ガイド生成のタスクを用意（pre_generated_guides への保存は
#    各サービス側の責務。ここでは委譲のみ）
# ------------------------------------------------------------

from __future__ import annotations

import base64
import traceback
from typing import Any, Dict, Optional

from shared.app.celery_app import celery_app
# タスク名は必ず shared.app.tasks の定数を使用（ズレ防止）
from shared.app.tasks import (
    TASK_ORCHESTRATE_CONVERSATION,
    TASK_START_NAVIGATION,
    TASK_UPDATE_LOCATION,
    TASK_PREGENERATE_GUIDES,
)

# DB 接続やスキーマ（必要に応じて使用）
from shared.app.database import SessionLocal  # noqa: F401
from shared.app import models, schemas  # noqa: F401

# 各サービス（Worker 側）をインポート
from worker.app.services.voice.voice_service import VoiceService
from worker.app.services.orchestration import state as orch_state
from worker.app.services.orchestration.graph import build_graph  # LangGraph 構築
from worker.app.services.navigation.navigation_service import NavigationService

# 必要に応じて利用（ナッジ・距離/時間などは内部で他サービスへ連携）
from worker.app.services.information.information_service import InformationService  # noqa: F401
from worker.app.services.itinerary.itinerary_service import ItineraryService  # noqa: F401
from worker.app.services.routing.routing_service import RoutingService  # noqa: F401

from shared.app.schemas import STTRequest, STTResult, TTSRequest, TTSResult


def _ensure_text_from_audio_if_needed(
    *,
    message_text: Optional[str],
    audio_b64: Optional[str],
    source_lang: Optional[str],
) -> str:
    """
    音声入力が来た場合は STT でテキスト化し、テキスト入力があれば優先する。
    """
    if message_text and message_text.strip():
        return message_text.strip()

    if audio_b64:
        # base64 -> bytes -> STT
        try:
            audio_bytes = base64.b64decode(audio_b64)
        except Exception:
            raise ValueError("音声データ(base64)のデコードに失敗しました。")

        vs = VoiceService()
        # Whisper で STT。source_lang が None の場合は自動検出を期待。
        text = vs.stt_handler.transcribe_audio_bytes(audio_bytes, language=source_lang)
        if not text or not text.strip():
            raise ValueError("音声のテキスト化に失敗しました。")
        return text.strip()

    raise ValueError("message_text も audio_b64 も指定されていません。")


# ============================
# Orchestration（会話中核タスク）
# ============================
@celery_app.task(name=TASK_ORCHESTRATE_CONVERSATION, bind=True)
def orchestrate_conversation_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    対話の全体オーケストレーションを実行する中核タスク。
    - 入力: { session_id, user_id, lang, input_mode, message_text?, audio_b64? }
    - 出力: { final_response, app_status, active_plan_id, ... }
    """
    try:
        session_id: str = payload.get("session_id")
        user_id: Optional[int] = payload.get("user_id")
        lang: str = payload.get("lang", "ja")
        input_mode: str = payload.get("input_mode", "text")  # "text" or "voice"
        message_text: Optional[str] = payload.get("message_text")
        audio_b64: Optional[str] = payload.get("audio_b64")

        if not session_id:
            raise ValueError("session_id は必須です。")

        # 1) 必要なら STT を実行してテキスト化
        latest_text = _ensure_text_from_audio_if_needed(
            message_text=message_text, audio_b64=audio_b64, source_lang=lang
        )

        # 2) グラフを構築（LangGraph）
        app = build_graph()

        # 3) State をロード
        agent_state = orch_state.load_agent_state(session_id=session_id)

        # 4) LangGraph を実行
        #    - nodes 内で Information/Itinerary/Routing/LLM などへ委譲される
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

        # 5) State を永続化（会話履歴・最終応答・アプリ状態など）
        orch_state.save_agent_state(session_id=session_id, agent_state=result_state)

        # 6) フロントに返す最小限の要約（Gateway がポーリングで取得する想定）
        return {
            "ok": True,
            "session_id": session_id,
            "app_status": result_state.get("app_status"),
            "active_plan_id": result_state.get("active_plan_id"),
            "final_response": result_state.get("final_response"),
        }

    except Exception as e:
        traceback.print_exc()
        return {
            "ok": False,
            "error": str(e),
        }


# ============================
# Navigation（開始/位置更新/事前生成）
# ============================
@celery_app.task(name=TASK_START_NAVIGATION, bind=True)
def navigation_start_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    ナビゲーション開始のトリガー。
    - 入力: { session_id, user_id, lang }
    - nodes / services 側でガイド文の事前生成を行い、pre_generated_guides へ保存する想定。
    """
    try:
        session_id: str = payload.get("session_id")
        user_id: Optional[int] = payload.get("user_id")
        lang: str = payload.get("lang", "ja")

        if not session_id:
            raise ValueError("session_id は必須です。")

        # State をナビゲーションモードへ移行＆ガイド文を事前生成
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
def navigation_location_update_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    位置情報の継続アップデート（ナビ実行中）。
    - 入力: { session_id, user_id, lat, lon }
    - NavigationService に委譲し、逸脱/接近の検知→必要に応じて
      オーケストレーションやリルート計算へ通知する。
    """
    try:
        session_id: str = payload.get("session_id")
        user_id: Optional[int] = payload.get("user_id")
        lat = payload.get("lat")
        lon = payload.get("lon")

        if not session_id:
            raise ValueError("session_id は必須です。")
        if lat is None or lon is None:
            raise ValueError("lat, lon は必須です。")

        nav = NavigationService()
        events = nav.process_tick(
            session_id=session_id,
            current_location={"lat": float(lat), "lon": float(lon)},
        )

        # NavigationService 側で、
        # - 逸脱検知→RoutingService のリルート計算トリガー
        # - 接近検知→pre_generated_guides から該当スポットのガイド取得→TTS
        # などを実行する想定（ここはあくまで委譲）

        return {
            "ok": True,
            "session_id": session_id,
            "events": events,  # 例: [{"type":"deviation_detected"}, {"type":"proximity", "spot_id":"..."}]
        }
    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "error": str(e)}


@celery_app.task(name=TASK_PREGENERATE_GUIDES, bind=True)
def pregenerate_guides_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    ガイド文を事前生成して保存するタスク。
    - 入力: { session_id, lang }
    - 実体は Orchestrator/LLM/Information の連携で生成し、DB (pre_generated_guides) に保存。
    """
    try:
        session_id: str = payload.get("session_id")
        lang: str = payload.get("lang", "ja")
        if not session_id:
            raise ValueError("session_id は必須です。")

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


# ============================
# Voice（STT/TTS 軽量ユーティリティ）
# ============================
_voice_service: Optional[VoiceService] = None


def _get_voice_service() -> VoiceService:
    global _voice_service
    if _voice_service is None:
        _voice_service = VoiceService()
    return _voice_service


@celery_app.task(name="voice.stt_transcribe", bind=True)
def stt_transcribe(self, payload: dict) -> dict:
    """
    base64 音声 → テキスト
    """
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


@celery_app.task(name="voice.tts_synthesize", bind=True)
def tts_synthesize(self, payload: dict) -> dict:
    """
    テキスト → base64 WAV
    """
    req = TTSRequest(**payload)
    service = _get_voice_service()
    audio_bytes, meta = service.synthesize(req.text, lang=req.lang)
    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
    return TTSResult(
        audio_b64=audio_b64,
        sample_rate=meta.get("sample_rate", 22050),
        lang=req.lang,
    ).model_dump()
