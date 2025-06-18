# /backend/worker/app/services/memory_service.py

import os
from typing import List, Tuple, Optional, Dict
from sqlalchemy.orm import Session
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, AIMessageChunk
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import OllamaEmbeddings
from langchain.schema import Document

# DBセッションとモデルをインポート
from app.db.session import SessionLocal
from app.db import models

# --- 設定項目 ---
# 短期記憶として取得する会話の最大数
SHORT_TERM_MEMORY_LIMIT = 10
# 長期記憶としてベクトル検索で取得する会話の数
LONG_TERM_MEMORY_K = 5 
# ベクトルDBの永続化先ディレクトリ
VECTORSTORE_PATH = "./vectorstore"
# ベクトル化に使用するモデル
EMBEDDINGS = OllamaEmbeddings(
    model="nomic-embed-text",
    base_url=os.getenv("OLLAMA_HOST", "http://localhost:11434") # デフォルトはlocalhost
)

# --- ベクトルデータベースの初期化 ---
# ChromaDBのインスタンスを一度だけ作成
vectorstore = Chroma(
    persist_directory=VECTORSTORE_PATH, 
    embedding_function=EMBEDDINGS
)

retriever = vectorstore.as_retriever(
    search_type="similarity",
    search_kwargs={"k": LONG_TERM_MEMORY_K}
)

# ==================== ヘルパー関数 ====================
def get_or_create_user(db: Session, user_id: str) -> models.User:
    """ユーザーが存在すれば取得し、存在しなければ作成するヘルパー関数。"""
    user = db.query(models.User).filter(models.User.user_id == user_id).first()
    if not user:
        print(f"User '{user_id}' not found. Creating new user.")
        user = models.User(user_id=user_id)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user

# ==================== 短期記憶 (SQL) ====================

def get_short_term_history(user_id: str) -> List[BaseMessage]:
    """
    指定されたユーザーの直近の会話履歴（短期記憶）をSQL DBから取得します。
    """
    db: Session = SessionLocal()
    try:
        history_from_db = (
            db.query(models.Conversation)
            .filter(models.Conversation.user_id == user_id)
            .order_by(models.Conversation.created_at.desc())
            .limit(SHORT_TERM_MEMORY_LIMIT)
            .all()
        )
        messages: List[BaseMessage] = []
        for record in reversed(history_from_db):
            if record.message_type == "human":
                messages.append(HumanMessage(content=record.content))
            else:
                messages.append(AIMessage(content=record.content))
        return messages
    finally:
        db.close()

def save_short_term_history(user_id: str, messages: List[BaseMessage]):
    """
    新しい会話のやり取りをSQL DBに保存します。
    """
    new_messages_to_save = messages[-2:]
    db: Session = SessionLocal()
    try:
        for msg in new_messages_to_save:
            # AIMessageChunkは文字列に変換できないため、通常のAIMessageに変換
            if isinstance(msg, AIMessageChunk):
                msg = AIMessage(content=str(msg.content))
            
            message_type = "human" if isinstance(msg, HumanMessage) else "ai"
            db_conversation = models.Conversation(
                user_id=user_id,
                message_type=message_type,
                content=str(msg.content)
            )
            db.add(db_conversation)
        db.commit()
    finally:
        db.close()

# ==================== 長期記憶 (ベクトルDB) ====================

def get_long_term_memory(user_id: str, query: str) -> List[BaseMessage]:
    """
    現在のクエリに最も関連する過去の会話（長期記憶）をベクトルDBから取得します。
    ★重要: `user_id`でフィルタリングして、他のユーザーの会話を検索しないようにします。
    """
    # メタデータでフィルタリングするリトリーバーを動的に作成
    filtered_retriever = vectorstore.as_retriever(
        search_kwargs={"filter": {"user_id": user_id}, "k": LONG_TERM_MEMORY_K}
    )
    
    # ベクトル検索を実行
    retrieved_docs: List[Document] = filtered_retriever.invoke(query)

    # 取得したDocumentをLangChainのMessageオブジェクトに変換
    messages: List[BaseMessage] = []
    for doc in retrieved_docs:
        message_type = doc.metadata.get("message_type", "human")
        if message_type == "human":
            messages.append(HumanMessage(content=doc.page_content))
        else:
            messages.append(AIMessage(content=doc.page_content))

    return messages

def save_long_term_memory(user_id: str, messages: List[BaseMessage]):
    """
    新しい会話のやり取りをベクトル化して、長期記憶としてベクトルDBに保存します。
    """
    new_messages_to_save = messages[-2:]
    docs_to_save: List[Document] = []
    for msg in new_messages_to_save:
        # AIMessageChunkは文字列に変換できないため、通常のAIMessageに変換
        if isinstance(msg, AIMessageChunk):
            msg = AIMessage(content=str(msg.content))

        message_type = "human" if isinstance(msg, HumanMessage) else "ai"
        # ★重要: user_idをメタデータに含める
        doc = Document(
            page_content=str(msg.content),
            metadata={"user_id": user_id, "message_type": message_type}
        )
        docs_to_save.append(doc)

    # ベクトルDBにドキュメントを追加
    if docs_to_save:
        vectorstore.add_documents(docs_to_save)

# ==================== その他のDB操作 ====================
def save_location(user_id: str, location: Tuple[float, float]):
    """
    ユーザーの現在位置をDBに保存します。
    """
    db: Session = SessionLocal()
    try:
        db_location = models.LocationHistory(
            user_id=user_id,
            latitude=location[0],
            longitude=location[1]
        )
        db.add(db_location)
        db.commit()
    finally:
        db.close()

def get_active_route(user_id: str) -> Optional[Dict]:
    """
    現在案内中のルート情報をDBから取得します。
    """
    db: Session = SessionLocal()
    try:
        active_route_record = db.query(models.ActiveRoute).filter(models.ActiveRoute.user_id == user_id).first()
        if active_route_record:
            return active_route_record.route_data
        return None
    finally:
        db.close()