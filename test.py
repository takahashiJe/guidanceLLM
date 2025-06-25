import os
from langchain_ollama import ChatOllama
from langchain_core.messages import SystemMessage, HumanMessage

SYSTEM_PROMPT = """あなたは「鳥海山ガイドAI」、ベテランの山岳ガイドです。
ユーザーの安全を第一に考え、常に丁寧で、正確かつ分かりやすい情報を提供してください。
"""

# エージェントで使っているのと同じ設定でLLMを初期化
llm = ChatOllama(
    # model="qwen2.5:32b-instruct",
    model="gemma3:27b-it-qat",
    base_url=os.getenv("http://ollama:11434"),
    temperature=0.1 # 自己紹介のような事実に基づく応答を見たいので、temperatureを少し下げます
)

# システムプロンプトと単純な質問のリストを作成
messages = [
    SystemMessage(content=SYSTEM_PROMPT),
    HumanMessage(content="あなたは誰ですか？"),
]

print("--- Calling LLM with simple prompt ---")
print("llm.model=" + llm.model)
# LLMを直接呼び出す
response = llm.invoke(messages)

print("\n--- LLM Response ---")
print(response.content)