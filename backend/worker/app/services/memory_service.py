# /backend/worker/app/services/memory_service.py

import os
import chromadb
from typing import List, Tuple, Optional, Dict
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, AIMessageChunk
from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import OllamaEmbeddings
from langchain.schema import Document

# DBセッションとモデルをインポート
from backend.worker.app.db.session import SessionLocal
from backend.worker.app.db import models

# --- 設定項目 ---
SHORT_TERM_MEMORY_LIMIT = 10
LONG_TERM_MEMORY_K = 5 
VECTORSTORE_PATH = "./vectorstore"
EMBEDDINGS = OllamaEmbeddings(
    model="nomic-embed-text",
    base_url=os.getenv("OLLAMA_HOST", "http://localhost:11434")
)

# --- ベクトルデータベースの初期化 ---
client = chromadb.PersistentClient(path=VECTORSTORE_PATH)

vectorstore = Chroma(
    client=client,
    collection_name="langchain", # デフォルトのコレクション名
    embedding_function=EMBEDDINGS,
)

# ==================== ヘルパー関数 ====================
def get_or_create_user(db: Session, client_user_id: str) -> models.User:
    """
    クライアントから渡された文字列のIDを元にユーザーを取得または作成する。
    注意: この関数はDBへのコミットを行わない。呼び出し側が責任を持つ。
    """
    # プレースホルダー'string'を、より意味のあるIDに変換
    if client_user_id == 'string':
        client_user_id = 'default-anonymous-user'

    # 文字列ID (user_id カラム) を使ってユーザーを検索
    user = db.query(models.User).filter(models.User.user_id == client_user_id).first()
    
    if not user:
        print(f"User with user_id='{client_user_id}' not found. Creating new user.")
        # 新しいUserオブジェクトを作成。文字列IDを保存する
        user = models.User(user_id=client_user_id)
        db.add(user)
        # flushしてDBセッション内で自動採番された整数ID (user.id) を確定させる
        db.flush() 
    return user

# ==================== 短期記憶 (SQL) ====================

def get_short_term_history(db: Session, user_id: str) -> List[BaseMessage]:
    """
    指定されたユーザーの直近の会話履歴（短期記憶）をSQL DBから取得します。
    """
    # 文字列のuser_idでユーザーを検索
    user = db.query(models.User).filter(models.User.user_id == user_id).first()
    if not user:
        return []

    # 取得したユーザーの整数ID (user.id) を使って会話を検索
    history_from_db = (
        db.query(models.Conversation)
        .filter(models.Conversation.user_id == user_id) # user.idではなく、引数の文字列user_idを直接使用
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
        

def save_short_term_history(db: Session, user_id: str, messages: List[BaseMessage]):
    """
    新しい会話のやり取りをSQL DBに保存します。
    """
    new_messages_to_save = messages[-2:]
    try:
        # 1. 文字列IDを元に、Userオブジェクト（整数IDを含む）を取得または作成
        user = get_or_create_user(db, user_id)
        
        # 2. 会話履歴を保存する
        for msg in new_messages_to_save:
            if isinstance(msg, AIMessageChunk):
                msg = AIMessage(content=str(msg.content))
            
            message_type = "human" if isinstance(msg, HumanMessage) else "ai"
            db_conversation = models.Conversation(
                user_id=user.user_id, # ★★★ 確実に存在する整数のuser.idを使用 ★★★
                message_type=message_type,
                content=str(msg.content)
            )
            db.add(db_conversation)

        # 3. ユーザー作成と会話保存をまとめてコミット
        # db.commit()
    except IntegrityError as e:
        print(f"Database integrity error: {e}")
        db.rollback()
        raise

# ==================== 長期記憶 (ベクトルDB) ====================

def get_long_term_memory(user_id: str, query: str) -> List[BaseMessage]:
    """
    現在のクエリに最も関連する過去の会話（長期記憶）をベクトルDBから取得します。
    """
    retrieved_docs: List[Document] = vectorstore.similarity_search(
        query=query,
        k=LONG_TERM_MEMORY_K,
        filter={"user_id": user_id}
    )
    
    filtered_retriever = vectorstore.as_retriever(
        search_kwargs={"filter": {"user_id": user_id}, "k": LONG_TERM_MEMORY_K}
    )
    retrieved_docs: List[Document] = filtered_retriever.invoke(query)
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
        if isinstance(msg, AIMessageChunk):
            msg = AIMessage(content=str(msg.content))
        message_type = "human" if isinstance(msg, HumanMessage) else "ai"
        doc = Document(
            page_content=str(msg.content),
            metadata={"user_id": user_id, "message_type": message_type}
        )
        docs_to_save.append(doc)

    if docs_to_save:
        vectorstore.add_documents(docs_to_save)

# ==================== その他のDB操作（修正） ====================
def save_location(db: Session, user_id: str, location: Tuple[float, float]):
    """
    ユーザーの現在位置をDBに保存します。
    """
    try:
        user = get_or_create_user(db, user_id)
        db_location = models.LocationHistory(
            user_id=user.user_id, # 確実に存在する整数のuser.idを使用
            latitude=location[0],
            longitude=location[1]
        )
        db.add(db_location)
        db.commit()
    except IntegrityError as e:
        print(f"Database integrity error: {e}")
        db.rollback()
        raise

def get_active_route(user_id: str) -> Optional[Dict]:
    """
    現在案内中のルート情報をDBから取得します。
    """
    try:
        user = db.query(models.User).filter(models.User.user_id == user_id).first()
        if not user:
            return None
        active_route_record = db.query(models.ActiveRoute).filter(models.ActiveRoute.user_id == user.id).first()
        if active_route_record:
            return active_route_record.route_data
        return None
    finally:
        db.close()