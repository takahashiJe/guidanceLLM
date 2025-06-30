# backend/worker/app/rag/retriever.py

import os
from typing import List

from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import OllamaEmbeddings
from langchain_community.document_loaders import DirectoryLoader, UnstructuredMarkdownLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document

# --- 設定項目 ---

# ベクトル化（Embedding）に使用するモデル。nodes.pyと同じモデルを推奨。
# 環境変数からOLLAMAのホスト名を取得
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
EMBEDDINGS_MODEL = "nomic-embed-text"

# ベクトルDB（Chroma）の永続化先ディレクトリ。worker/data内に作成するのが管理上望ましい。
VECTORSTORE_BASE_PATH = "./app/data/vectorstore"

# RAGの知識源となるドキュメントが格納されているディレクトリ
KNOWLEDGE_BASE_PATH = "./app/data/knowledge"

# テキストを分割する際のチャンクサイズとオーバーラップ
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100

# --- グローバル変数 ---
# OllamaEmbeddingsのインスタンスを一度だけ生成
embeddings = OllamaEmbeddings(model=EMBEDDINGS_MODEL, base_url=OLLAMA_HOST)

# --- 初期セットアップ関数 ---

def setup_vectorstore():
    """
    アプリケーションの初回起動時に一度だけ実行されるべき関数。
    data/knowledgeディレクトリ内の言語別ドキュメントを読み込み、
    言語ごとに独立したベクトルデータベース（コレクション）を構築します。
    """
    print("--- RAG: Setting up vector stores... ---")
    
    # 知識ベースのディレクトリが存在しない場合は何もしない
    if not os.path.exists(KNOWLEDGE_BASE_PATH):
        print(f"Knowledge base directory not found at {KNOWLEDGE_BASE_PATH}. Skipping setup.")
        return
        
    # knowledgeディレクトリ内の言語フォルダ（ja, en, zhなど）をスキャン
    for lang in os.listdir(KNOWLEDGE_BASE_PATH):
        lang_path = os.path.join(KNOWLEDGE_BASE_PATH, lang)
        if not os.path.isdir(lang_path):
            continue

        collection_name = f"knowledge_{lang}"
        persist_directory = os.path.join(VECTORSTORE_BASE_PATH, lang)

        # 既にDBが存在する場合はセットアップをスキップ
        if os.path.exists(persist_directory) and os.listdir(persist_directory):
            print(f"Vector store for language '{lang}' already exists. Skipping setup.")
            continue

        print(f"Creating vector store for language '{lang}'...")
        
        # 1. ドキュメントの読み込み
        #    指定された言語のディレクトリからMarkdownファイルをすべて再帰的に読み込む
        loader = DirectoryLoader(
            lang_path,
            glob="**/*.md",
            loader_cls=UnstructuredMarkdownLoader,
            show_progress=True
        )
        docs = loader.load()

        if not docs:
            print(f"No documents found for language '{lang}'.")
            continue

        # 2. ドキュメントの分割
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP
        )
        splits = text_splitter.split_documents(docs)

        # 3. ベクトル化とDBへの保存
        print(f"Embedding {len(splits)} document splits for language '{lang}'...")
        Chroma.from_documents(
            documents=splits,
            embedding=embeddings,
            collection_name=collection_name,
            persist_directory=persist_directory
        )
        print(f"Successfully created vector store for language '{lang}'.")

# --- クエリ実行関数 ---

def query_rag_and_get_docs(query: str, language: str = "ja", k: int = 5) -> List[Document]:
    """
    指定された言語のベクトルDBに対してクエリを実行し、関連性の高いDocumentオブジェクトのリストを取得します。
    multi_rag_retrieval_nodeから呼び出されます。
    """
    collection_name = f"knowledge_{language}"
    persist_directory = os.path.join(VECTORSTORE_BASE_PATH, language)
    
    # データベースディレクトリが存在しない場合は空のリストを返す
    if not os.path.exists(persist_directory):
        print(f"Warning: Vector store for language '{language}' not found.")
        return []

    try:
        # 永続化されたDBからChromaインスタンスを読み込む
        vectorstore = Chroma(
            persist_directory=persist_directory,
            embedding_function=embeddings,
            collection_name=collection_name
        )

        # リトリーバーを作成し、類似度検索を実行
        retriever = vectorstore.as_retriever(search_kwargs={"k": k})
        retrieved_docs: List[Document] = retriever.invoke(query)
        
        return retrieved_docs
    except Exception as e:
        # DBが空の場合などにエラーが発生する可能性があるため
        print(f"Error querying RAG for language '{language}': {e}")
        return []


# --- アプリケーション起動時の処理 ---
# このモジュールがインポートされたときに、ベクトルDBのセットアップを実行します。
# これにより、ワーカープロセスの起動時に一度だけDBの構築が行われます。
setup_vectorstore()