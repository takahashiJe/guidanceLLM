#agent_chain.py

from langchain.agents import initialize_agent, AgentType
from langchain.memory import ConversationBufferMemory
from langchain_ollama import ChatOllama
from langchain_community.llms import HuggingFacePipeline
from langchain.tools import StructuredTool
from langchain.schema import SystemMessage
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain.prompts.chat import ChatPromptTemplate, MessagesPlaceholder, SystemMessagePromptTemplate, HumanMessagePromptTemplate
# from transformers import Gemma3ForConditionalGeneration, AutoProcessor

from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
from .tools import select_best_route
import torch

SYSTEM_PROMPT = """
    あなたは「鳥海山ガイドAI」、山岳ガイドです。
    ユーザーの安全を第一に考え、常に丁寧で、正確かつ分かりやすい情報を提供してください。

    あなたの能力は以下の通りです:
    - ルート案内 (`route_guide`): 現在地と目的地から最適なルートを提案します。
    - スポット情報の提供 (`spot_info_finder`): 山小屋や名所の詳細を説明します。
    - 鳥海山の知識 (`chokaisan_rag_retriever`): 歴史、自然、文化に関する質問に答えます。
    """

# ChatPromptTemplate を構成
prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    MessagesPlaceholder(variable_name="messages"),
    MessagesPlaceholder(variable_name="agent_scratchpad")
])

llm = ChatOllama(
        # model="qwen2.5:32b-instruct",
        model="gemma3:27b-it-qat",
        # model="gemma3:27b",
        # model="llama3:70b",
        # model="elyza-jp-chat",
        base_url="http://ollama:11434",
        temperature=0.7
    )

route_tool = StructuredTool.from_function(
        name="route_selector",
        func=select_best_route,
        description=(
            "Suggests a hiking route for Mount Chokai (鳥海山) "
            "based on the user's fitness, if they are with children, and desired time."
        )
    )

#tools = [route_tool]
tools = []

def create_route_agent():
    memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)
    agent = create_tool_calling_agent(
        llm=llm,
        tools=tools,
        prompt=prompt
    )
    agent_executor = AgentExecutor.from_agent_and_tools(
        agent=agent,
        tools=tools,
        memory=memory,
        verbose=True,
        handle_parsing_errors=True
    )

    return agent_executor

if __name__ == '__main__':
    agent = create_route_agent()
    response = agent.invoke({"input": "おすすめのルートは？"})
    print(response["output"])
