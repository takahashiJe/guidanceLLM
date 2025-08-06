# worker/app/services/orchestration/state.py

from uuid import UUID
from sqlalchemy.orm import Session
from langchain_core.messages import HumanMessage, AIMessage

from shared.app.models import Session as SessionModel, ConversationHistory
from shared.app.schemas import AgentState
from shared.app.database import session_scope

def load_state(session_id: UUID) -> AgentState:
    """
    指定されたsession_idに基づいて、DBから現在の状態をロードし、AgentStateオブジェクトを構築する。
    """
    with session_scope() as db:
        # 1. セッション情報を取得
        session_data = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
        if not session_data:
            # 本来はAPI Gateway層でセッションが作られるため、ここには到達しない想定
            raise ValueError(f"Session with id {session_id} not found.")

        # 2. 会話履歴を取得し、LangChainのメッセージ形式に変換
        history = (
            db.query(ConversationHistory)
            .filter(ConversationHistory.session_id == session_id)
            .order_by(ConversationHistory.turn)
            .all()
        )
        chat_history = []
        for turn in history:
            if turn.user_input:
                chat_history.append(HumanMessage(content=turn.user_input))
            if turn.ai_output:
                chat_history.append(AIMessage(content=turn.ai_output))

        # 3. AgentStateを構築して返す
        initial_state: AgentState = {
            "userId": str(session_data.user_id),
            "sessionId": str(session_data.session_id),
            "language": "ja",  # TODO: セッションから言語設定を読み込む
            "interactionMode": "text", # TODO: セッションから対話モードを読み込む
            "appStatus": session_data.app_status,
            "chatHistory": chat_history,
            "finalResponse": "",
            "activePlanId": session_data.active_plan_id,
            "intermediateData": {},
            "userInput": "", # ユーザーの最新の入力を保持するフィールドを追加
        }
        return initial_state

def save_state(state: AgentState):
    """
    対話の1ターンが完了した後、更新された状態をDBに永続化する。
    主に最新の会話履歴を保存する。
    """
    # ユーザー入力とAI応答のペアがなければ保存しない
    if not state.get("userInput") or not state.get("finalResponse"):
        return

    with session_scope() as db:
        session_id = UUID(state["sessionId"])
        
        # 最後のターン番号を取得
        last_turn = db.query(ConversationHistory.turn).filter(ConversationHistory.session_id == session_id).order_by(ConversationHistory.turn.desc()).first()
        new_turn_number = (last_turn[0] if last_turn else 0) + 1

        # 新しい会話履歴を作成して保存
        new_history = ConversationHistory(
            session_id=session_id,
            turn=new_turn_number,
            user_input=state["userInput"],
            ai_output=state["finalResponse"],
        )
        db.add(new_history)
        
        # セッションの状態（appStatusなど）もここで更新できる
        # session_to_update = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
        # if session_to_update:
        #     session_to_update.app_status = state["appStatus"]