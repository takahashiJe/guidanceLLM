# shared/app/schemas.py

from typing import TypedDict, List, Optional, Any
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    """
    LangGraphのグラフ全体で受け渡される状態オブジェクト。
    FR-1, FR-2, FR-6, FR-7の要件を管理する。
    """
    # === セッション情報 ===
    # FR-1-1: 認証されたユーザーのID
    userId: str
    
    # FR-1-2: 現在の会話セッションのID
    sessionId: str
    
    # FR-6-1: ユーザーが選択した言語 ('ja', 'en', 'zh')
    language: str

    # FR-7-3: 現在の対話モード ('text' or 'voice')
    interactionMode: str
    
    # FR-1-3: 現在のアプリの状態 ('Browse', 'planning', 'navigating')
    appStatus: str
    
    # === 対話情報 ===
    # FR-2-2: LLMに渡す直近の会話履歴
    chatHistory: List[BaseMessage]

    # フロントエンドに返却する、AIの最終応答テキスト
    finalResponse: str

    # === 計画情報 ===
    # FR-4: 現在編集中の周遊計画ID
    activePlanId: Optional[int]
    
    # --- 内部処理用 ---
    
    # ノード間で受け渡す一時的なデータ
    # (例: Agentic RAGで収集した情報、推薦候補スポットリストなど)
    intermediateData: dict