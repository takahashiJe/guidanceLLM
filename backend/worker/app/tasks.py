# /backend/worker/app/tasks.py
import sys
import os
from langchain_core.messages import HumanMessage
from typing import List
from langchain_core.messages import BaseMessage

# 共有モジュールからCeleryインスタンスとスキーマをインポート
from shared.celery_app import celery_app
from shared.schemas import ChatRequest, UpdateLocationRequest, ChatResponse, UpdateLocationResponse
from shared.state import GraphState

# ワーカー内部のモジュールをインポート
from app.graph.build_graph import compiled_graph
from app.services import memory_service, route_service, planning_service # DBアクセスやビジネスロジックを担当するサービス
from app.db.session import SessionLocal # DBセッションを直接利用する場合


@celery_app.task
def process_chat_message(request_data: dict) -> dict:
    """
    ユーザーからのチャットメッセージを処理するメインタスク。
    LangGraphを実行し、応答を生します。
    """
    db = SessionLocal()
    try:
        request = ChatRequest(**request_data) # リクエストデータをPydanticモデルに変換

        # 1. 短期記憶と長期記憶を取得
        short_term_memory = memory_service.get_short_term_history(db, request.user_id)
        long_term_memory = memory_service.get_long_term_memory(
            user_id=request.user_id, 
            query=request.message
        )

        # 2. 現在の訪問計画を取得
        plan = planning_service.get_plan(db, request.user_id)
        visit_plan_data = None
        if plan:
            visit_plan_data = {
                "spot_name": plan.spot_name,
                "visit_date": plan.visit_date.isoformat()
            }

        # LangGraphの初期状態を作成
        initial_state: GraphState = {
            "messages": long_term_memory + short_term_memory + [HumanMessage(content=request.message)],
            "task_status": request.task_status,
            "language": request.language,
            "user_id": request.user_id,
            "current_location": request.current_location,
            "visit_plan": visit_plan_data,
            # Noneで初期化
            "intent": None,
            "tool_outputs": None,
            "final_answer": None,
            "action_payload": None,
        }

        # LangGraphを実行
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
        memory_service.save_short_term_history(db, request.user_id, final_state["messages"])
        memory_service.save_long_term_memory(request.user_id, final_state["messages"])
        db.commit()
        # 7. 結果を辞書形式で返す (Celeryはシリアライズ可能なデータを返す必要がある)
        return response.model_dump()

    except Exception as e:
        # エラーが発生したらロールバックする
        db.rollback()
        # エラーをログに出力し、再raiseしてCeleryにタスクの失敗を通知する
        print(f"Error in process_chat_message: {e}")
        # traceback.print_exc() # さらに詳細なトレースバックが必要な場合
        raise
    finally:
        # タスクの成功・失敗に関わらず、必ずセッションを閉じる
        db.close()

@celery_app.task
def process_location_update(request_data: dict) -> dict:
    """
    ユーザーの位置情報更新を処理するタスク。
    """
    db = SessionLocal()
    try:
        request = UpdateLocationRequest(**request_data)

        # 案内中でなければ何もせず終了
        if request.task_status != "guiding":
            return UpdateLocationResponse().model_dump()

        # 1. 位置情報をDBに保存
        memory_service.save_location(db, request.user_id, request.current_location)

        # 2. 案内中のルート情報をDBから取得
        active_route = memory_service.get_active_route(db, request.user_id)
        if not active_route:
            db.rollback() # 追加した位置情報を取り消し
            return UpdateLocationResponse().model_dump()
        
        db.commit()
        
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
                "task_status": "guiding"
                }
            final_state = compiled_graph.invoke(initial_state)
            intervention_message = final_state["messages"][-1].content
            
            response = UpdateLocationResponse(
                intervention_message=intervention_message,
                new_task_status=final_state.get("task_status")
            )
            return response.model_dump()

    except Exception as e:
        db.rollback() # エラーが発生したらロールバック
        print(f"Error in process_location_update: {e}")
        traceback.print_exc() # エラーの詳細なトレースバックを出力
        raise
    finally:
        db.close() # 必ずセッションを閉じる