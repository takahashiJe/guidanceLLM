# worker/app/tasks.py

from uuid import UUID
from celery.utils.log import get_task_logger
from typing import Optional, Dict, Any

# Celeryアプリケーションのインスタンスをインポート
from shared.app.celery_app import celery_app

# 各専門サービスとオーケストレーターのグラフをインポート
# (実際にはDIコンテナ等でインスタンスを管理するのが望ましい)
from worker.app.services.orchestration.graph import app as orchestration_graph
from worker.app.services.orchestration.state import load_state, save_state
# from worker.app.services.navigation.navigation_service import NavigationService # 実行時に動的に生成

# Celeryタスク用のロガーを取得
logger = get_task_logger(__name__)


@celery_app.task(name="orchestrate_conversation_task", bind=True, max_retries=3)
def orchestrate_conversation_task(self, *, session_id: str, user_id: int, text: Optional[str], audio_data: Optional[bytes]):
    """
    [メインタスク] ユーザーとの対話全体をオーケストレーションする。
    API Gatewayの /message エンドポイントから呼び出される。
    """
    logger.info(f"Task 'orchestrate_conversation_task' started for session_id: {session_id}")
    try:
        # TODO: audio_dataが渡された場合、ここで音声サービスを呼び出しテキストに変換する
        user_message = text # 現状はテキストのみを想定

        # 1. 対話の初期状態をDBからロードする
        initial_state = load_state(UUID(session_id))
        initial_state["userInput"] = user_message

        # 2. LangGraphで構築されたステートマシンを実行する
        final_state = None
        for event in orchestration_graph.stream(initial_state):
            for key, value in event.items():
                logger.debug(f"Graph node '{key}' executed for session {session_id}.")
                final_state = value

        # 3. 最終的な状態をDBに保存する
        if final_state:
            save_state(final_state)
            logger.info(f"Task 'orchestrate_conversation_task' finished successfully for session_id: {session_id}")
            return final_state.get("finalResponse", "No response generated.")
        else:
            raise ValueError("LangGraph did not produce a final state.")

    except Exception as e:
        logger.error(f"Error in orchestrate_conversation_task for session {session_id}: {e}", exc_info=True)
        # エラーが発生した場合、指数バックオフで再試行
        raise self.retry(exc=e, countdown=2 ** self.request.retries)


@celery_app.task(name="start_navigation_task", bind=True, max_retries=3)
def start_navigation_task(self, *, session_id: str, plan_id: int, user_id: int):
    """
    [ナビゲーションタスク] ナビゲーションセッションを開始する。
    API Gatewayの /navigation/start エンドポイントから呼び出される。
    """
    logger.info(f"Task 'start_navigation_task' started for session_id: {session_id}, plan_id: {plan_id}")
    try:
        # ここでオーケストレーターのナビゲーション開始フローを呼び出す
        # 例: orchestration_graph.invoke({"startNavigation": {"plan_id": plan_id, ...}})
        # このフロー内で、ルート計算、ガイド対象スポット特定、ガイドテキスト事前生成などが行われる。
        # そして、生成されたNavigationServiceインスタンスをセッションに紐づけてキャッシュに保存する。
        logger.info("--- (Placeholder) Orchestrator's navigation start flow would be called here. ---")
        
        logger.info(f"Task 'start_navigation_task' finished successfully for session_id: {session_id}")
        return {"status": "Navigation session initialized."}

    except Exception as e:
        logger.error(f"Error in start_navigation_task for session {session_id}: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=2 ** self.request.retries)


@celery_app.task(name="update_location_task", ignore_result=True)
def update_location_task(*, session_id: str, user_id: int, latitude: float, longitude: float):
    """
    [ナビゲーションタスク] ユーザーの現在地を更新し、イベントを処理する。
    API Gatewayの /navigation/location エンドポイントから呼び出される。
    """
    # このタスクは頻繁に呼び出されるため、ロギングは控えめにするか、DEBUGレベルにする
    # logger.debug(f"Task 'update_location_task' for session_id: {session_id}")
    try:
        # 1. キャッシュからセッションに紐づくNavigationServiceインスタンスを取得
        # nav_service = get_nav_service_from_cache(session_id)
        # if not nav_service:
        #     logger.warning(f"NavigationService not found in cache for session {session_id}. Task ignored.")
        #     return

        # 2. NavigationServiceに現在地を渡してイベントをチェック
        current_location = {"latitude": latitude, "longitude": longitude}
        # event = nav_service.update_user_location(current_location)
        
        # --- (Placeholder) 以下は本来のロジック ---
        event: Optional[Dict[str, Any]] = None # ダミー
        logger.info(f"--- (Placeholder) NavigationService would check for events at {current_location}. ---")
        # --- (Placeholderここまで) ---

        if event:
            logger.info(f"Event '{event['event_type']}' detected for session {session_id}.")
            # 3. イベントが発生した場合、オーケストレーターに処理を依頼する新しいタスクを投入
            # handle_navigation_event_task.delay(session_id=session_id, event=event)

    except Exception as e:
        # このタスクは失敗しても再試行しない（次の位置情報更新でカバーされるため）
        logger.error(f"Error in update_location_task for session {session_id}: {e}", exc_info=True)

