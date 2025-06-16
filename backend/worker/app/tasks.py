# /backend/worker/app/tasks.py
import os
from langchain_core.messages import HumanMessage

# 共有モジュールからCeleryインスタンスとスキーマをインポート
from shared.celery_app import celery_app
from shared.schemas import ChatRequest, UpdateLocationRequest, ChatResponse, UpdateLocationResponse
from shared.state import GraphState

# ワーカー内部のモジュールをインポート
from .graph.build_graph import compiled_graph
from .services import memory_service, route_service # DBアクセスやビジネスロジックを担当するサービス
from .db.session import SessionLocal # DBセッションを直接利用する場合

@celery_app.task(name="process_chat_message")
def process_chat_message(request_data: dict) -> dict:
    """
    ユーザーからのチャットメッセージを処理するメインタスク。
    LangGraphを実行し、応答を生します。
    """
    try:
        # 1. リクエストデータをPydanticモデルに変換
        request = ChatRequest(**request_data)

        # 2. 短期記憶を取得
        short_term_memory = memory_service.get_short_term_history(request.user_id)
        
        # 2.2 長期記憶を取得（現在のメッセージに関連する過去の会話）
        long_term_memory = memory_service.get_long_term_memory(
            user_id=request.user_id, 
            query=request.message
        )

        # 3. LangGraphの初期状態を作成
        initial_state: GraphState = {
            "messages": long_term_memory + short_term_memory + [HumanMessage(content=request.message)],
            "task_status": request.task_status,
            "language": request.language, # スキーマにlanguageを追加する必要があります
            "user_id": request.user_id,
            # その他のキーはNoneで初期化
            "intent": None,
            "tool_outputs": None,
            "final_answer": None,
            "action_payload": None,
        }

        # 4. LangGraphを実行
        final_state = compiled_graph.invoke(initial_state)

        # 5. 最終的な応答を構築
        #    agent_nodeが最終応答をAIMessageとしてmessagesの末尾に追加すると仮定
        final_ai_message = final_state["messages"][-1].content
        response = ChatResponse(
            answer_text=final_ai_message,
            task_status=final_state["task_status"],
            action=final_state.get("action_payload")
        )

        # 6. 更新された会話履歴をDBに保存
        memory_service.save_short_term_history(request.user_id, final_state["messages"])
        memory_service.save_long_term_memory(request.user_id, final_state["messages"])
        
        # 7. 結果を辞書形式で返す (Celeryはシリアライズ可能なデータを返す必要がある)
        return response.model_dump()

    except Exception as e:
        # エラーハンドリング
        print(f"Error in process_chat_message: {e}")
        # Celeryタスク内で例外を再発生させると、Celery側で失敗として扱われる
        raise

@celery_app.task(name="process_location_update")
def process_location_update(request_data: dict) -> dict:
    """
    ユーザーの位置情報更新を処理するタスク。
    """
    try:
        request = UpdateLocationRequest(**request_data)

        # 案内中でなければ何もせず終了
        if request.task_status != "guiding":
            return UpdateLocationResponse().model_dump()

        # 1. 位置情報をDBに保存
        memory_service.save_location(request.user_id, request.current_location)

        # 2. 案内中のルート情報をDBから取得
        active_route = memory_service.get_active_route(request.user_id)
        if not active_route:
            return UpdateLocationResponse().model_dump()
        
        # 3. ルート逸脱などをチェック
        progress_status = route_service.check_user_progress(
            current_location=request.current_location,
            route_data=active_route
        )

        # 4. AIの介入が必要な場合のみLangGraphを実行
        if progress_status["status"] == "on_route":
            return UpdateLocationResponse().model_dump()
        else:
            # 介入が必要な状況を説明するメッセージでグラフを起動
            system_trigger_message = f"状況: {progress_status['status']}. ユーザーに伝えるべき適切なメッセージを生成してください。"
            initial_state = {
                "messages": [HumanMessage(content=system_trigger_message)],
                "task_status": "guiding",
                # ...
            }
            final_state = compiled_graph.invoke(initial_state)
            intervention_message = final_state["messages"][-1].content
            
            response = UpdateLocationResponse(
                intervention_message=intervention_message,
                new_task_status=final_state.get("task_status")
            )
            return response.model_dump()

    except Exception as e:
        print(f"Error in process_location_update: {e}")
        raise