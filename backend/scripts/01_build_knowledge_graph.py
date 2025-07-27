# /backend/scripts/01_build_knowledge_graph.py
# Graph RAG のための知識グラフ構築スクリプトです

import os
import json
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_ollama import ChatOllama
from backend.worker.app.rag.retriever import KNOWLEDGE_BASE_PATH # ragからパスをインポート

# LLMとパーサーを初期化
llm = ChatOllama(
        # model="qwen2.5:32b-instruct",
        model="gemma3:27b-it-qat",
        # model="gemma3:27b",
        # model="llama3:70b",
        # model="elyza-jp-chat",
        base_url="http://ollama:11434",
        base_url=os.getenv("OLLAMA_HOST", "http://ollama:11434"), 
        format="json"
    )
json_parser = JsonOutputParser()

# 知識抽出用のプロンプト
extraction_prompt = ChatPromptTemplate.from_template(
    """あなたはテキストから知識を抽出し、グラフ構造として表現する専門家です。
    以下のテキストを読み、そこに含まれる重要なエンティティ（場所、施設、コース名など）と、それらの間の関係性を特定してください。
    結果は(エンティティ1, 関係, エンティティ2)という形式のタプルのリストとして、JSONで出力してください。
    関係性の例: "is_near", "has_feature", "part_of_course", "access_point"
   
   テキスト:
    ---
    {text_chunk}
    ---

    抽出結果:
    """
    )

extraction_chain = extraction_prompt | llm | json_parser

def extract_knowledge_from_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        text = f.read()
    # テキストをチャンクに分割するロジック（簡易版）
    chunks = [text[i:i+1000] for i in range(0, len(text), 1000)]
    
    all_triplets = []
    for chunk in chunks:
        try:
            triplets = extraction_chain.invoke({"text_chunk": chunk})
            if isinstance(triplets, list):
                all_triplets.extend(triplets)
        except Exception as e:
            print(f"Error processing chunk: {e}")
    return all_triplets

def main():
    print("Starting knowledge graph extraction...")
    output_file = "./backend/worker/data/graph_data.jsonl"
    
    with open(output_file, 'w', encoding='utf-8') as f_out:
        for lang in os.listdir(KNOWLEDGE_BASE_PATH):
            lang_path = os.path.join(KNOWLEDGE_BASE_PATH, lang)
            if not os.path.isdir(lang_path):
                continue
            
            for root, _, files in os.walk(lang_path):
                for file in files:
                    if file.endswith(".md"):
                        file_path = os.path.join(root, file)
                        print(f"Processing file: {file_path}")
                        triplets = extract_knowledge_from_file(file_path)
                        for triplet in triplets:
                            f_out.write(json.dumps({"triplet": triplet, "source": file_path}) + "\n")
                            
    print(f"Knowledge graph data saved to {output_file}")

if __name__ == "__main__":
    main()