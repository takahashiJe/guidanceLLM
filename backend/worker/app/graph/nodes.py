# /backend/app/graph/nodes.py

from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_ollama import ChatOllama
from ..shared/state import GraphState
from .tools import available_tools

# --- Agentのセットアップ ---
# この部分はアプリケーション起動時に一度だけ実行されるのが望ましい
SYSTEM_PROMPT = """あなたは「鳥海山ガイドAI」、ベテランの山岳ガイドです。
ユーザーの安全を第一に考え、常に丁寧で、正確かつ分かりやすい情報を提供してください。
ユーザーの状態(`task_status`)を考慮して、適切な応答をしてください。
"""
prompt = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    MessagesPlaceholder(variable_name="messages"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
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
agent = create_tool_calling_agent(llm, available_tools, prompt)
agent_executor = AgentExecutor(agent=agent, tools=available_tools, verbose=True)


# --- ノード関数の定義 ---

def agent_node(state: GraphState) -> dict:
    """中心的なエージェントノード。LLMが思考し、ツール呼び出しや最終応答を決定する。"""
    # agent_executorを呼び出すロジックを追記
    result = agent_executor.invoke(state)
    return {"messages": [AIMessage(content=result["output"])]}

def tool_executor_node(state: GraphState) -> dict:
    """Agentが決定したツールを実行し、その結果をToolMessageとして返す。"""
    # ツール呼び出しを実行し、結果を返すロジックを追記
    tool_calls = state['messages'][-1].tool_calls
    tool_messages = []
    for tool_call in tool_calls:
        tool_name = tool_call["name"]
        # 対応するツールを探して実行
        tool_to_call = next(filter(lambda t: t.name == tool_name, available_tools))
        tool_output = tool_to_call.invoke(tool_call["args"])
        # 結果をToolMessage形式で格納
        tool_messages.append(ToolMessage(content=str(tool_output), tool_call_id=tool_call['id']))
    return {"messages": tool_messages}

def classify_intent_node(state: GraphState) -> dict:
    """ユーザーのメッセージから意図を分類する。"""
    # 入力: state['messages']
    # 出力: {"intent": "greeting" | "plan_visit_request" | ...}
    pass

def classify_confirmation_node(state: GraphState) -> dict:
    """ルート提案に対するユーザーの応答が肯定的か否定的かを分類する。"""
    pass

def propose_route_node(state: GraphState) -> dict:
    """ツールが計算したルート情報を基に、ユーザーへの確認メッセージを生成し、状態を更新する。"""
    pass

def start_guidance_node(state: GraphState) -> dict:
    """ルート案内にユーザーが合意した際の開始メッセージを生成し、状態を更新する。"""
    pass

def handle_visit_plan_result_node(state: GraphState) -> dict:
    """`check_and_plan_visit`ツールの結果に応じて応答を生成する。"""
    # 入力: state['tool_outputs']
    # 出力: {"messages": [AIMessage(...)]}
    pass

def generate_simple_response_node(state: GraphState) -> dict:
    """雑談や簡単な質問に対する応答を生成する。"""
    pass