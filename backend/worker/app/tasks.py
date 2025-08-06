# worker/app/tasks.py

from uuid import UUID
from celery.utils.log import get_task_logger

from shared.app.celery_app import celery_app
from worker.app.services.orchestration.graph import app as aget_app
from worker.app.services.orchestration.state import load_state, save_state

# Celeryタスク用のロガーを取得
logger = get_task_logger(__name__)


@celery_app.task(name="orchestrate_conversation_task")
def orchestrate_conversation_task(
    user_id: int,
    session_id_str: str,
    user_message: str
):
    """
    ユーザーとの対話全体をオーケストレーションするメインのCeleryタスク。

    このタスクの役割:
    1. 対話の初期状態をDBからロードする。
    2. LangGraphで構築されたステートマシンを実行する。
    3. 最終的な状態をDBに保存する。

    Args:
        user_id (int): 現在のユーザーID。
        session_id_str (str): 現在のセッションID（文字列）。
        user_message (str): ユーザーからの最新のメッセージ。
    """
    session_id = UUID(session_id_str)
    logger.info(f"Task started for session_id: {session_id}")

    try:
        # 1. 状態管理の責任者(state.py)を呼び出し、対話の初期状態をロード
        initial_state = load_state(session_id)
        initial_state["userInput"] = user_message

        # 2. 対話オーケストレーション部のグラフ（app）を実行
        # inputsに対話の初期状態を渡す
        # LangGraphの.stream()はイベントのストリームを返す
        final_state = None
        for event in aget_app.stream(initial_state):
            # ストリームの最後のイベントに最終状態が含まれている
            for key, value in event.items():
                logger.debug(f"Graph node '{key}' executed.")
                final_state = value

        # 3. 状態管理の責任者(state.py)を呼び出し、最終状態を保存
        if final_state:
            save_state(final_state)
            logger.info(f"Task finished successfully for session_id: {session_id}")
            # フロントエンドに返す最終応答をタスクの結果として返すことも可能
            return final_state.get("finalResponse", "No response generated.")
        else:
            logger.error(f"Task for session_id: {session_id} did not produce a final state.")
            return "Error: Could not process the request."

    except Exception as e:
        logger.error(f"An error occurred in orchestrate_conversation_task for session_id {session_id}: {e}", exc_info=True)
        # エラーが発生した場合、タスクを再試行させることも可能
        raise