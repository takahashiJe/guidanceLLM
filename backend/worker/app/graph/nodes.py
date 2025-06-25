# /backend/app/graph/nodes.py

import os
import ast 
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import SystemMessage, AIMessage, ToolMessage
from langchain_core.output_parsers import StrOutputParser, JsonOutputParser
from langchain_ollama import ChatOllama
from shared.state import GraphState
from app.graph.tools import available_tools
from app.rag import retriever

# --- Agentのセットアップ ---
# この部分はアプリケーション起動時に一度だけ実行されるのが望ましい
SYSTEM_PROMPT = """あなたは「鳥海山ガイドAI」、ベテランの山岳ガイドです。
ユーザーの安全を第一に考え、常に丁寧で、正確かつ分かりやすい情報を提供してください。
【あなたの行動原則】
1.  **地名の正規化**: ユーザーからルートに関する質問を受けたら、まず `normalize_location_names` ツールを使い、曖昧な地名を正式名称に変換してください。
2.  **ルート計算**: 正規化された地名を使って `calculate_route` ツールを呼び出してください。
3.  **エラー対応**: `calculate_route`ツールの結果が `{'error': 'unsupported_location', ...}` だった場合、それはデータが存在しないことを意味します。その際は、ツールを再試行するのではなく、「申し訳ありません、実際の道案内サービスは現在、祓川周辺のトレイルコースのみ対応しております。」と丁寧に謝罪してください。
4.  **通常のツール利用**: その他の質問に対しては、適宜 `knowledge_base_search` や `check_and_plan_visit` などのツールを利用してください。
"""

llm = ChatOllama(
        model="qwen2.5:32b-instruct",
        # model="gemma3:27b-it-qat",
        # model="gemma3:27b",
        # model="llama3:70b",
        # model="elyza-jp-chat",
        base_url=os.getenv("OLLAMA_HOST", "http://ollama:11434"),
        temperature=0.7
        )

prompt = ChatPromptTemplate.from_messages([
    MessagesPlaceholder(variable_name="messages"),
    MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])

agent = create_tool_calling_agent(llm, available_tools, prompt)
agent_executor = AgentExecutor(
    agent=agent, 
    tools=available_tools, 
    verbose=True, 
    handle_parsing_errors=True
    )


# --- ノード関数の定義 ---

# クエリ拡張ノード
def query_expansion_node(state: GraphState) -> dict:
    """ユーザーの質問を、より検索に適した複数の質問に拡張する。"""
    user_message = state["messages"][-1].content
    
    prompt_template = ChatPromptTemplate.from_template(
        """あなたはユーザーの質問を分析し、関連情報を多角的に検索するための3つの異なる質問を生成する専門家です。
        元の質問の意図を保ちつつ、背景、具体的な側面、関連するトピックなどを網羅するような質問を生成してください。
        JSON形式のリストで回答してください。例: ["質問1", "質問2", "質問3"]

        元の質問: "{message}"
        """
        )
    
    expansion_chain = prompt_template | llm | JsonOutputParser()
    expanded_queries = expansion_chain.invoke({"message": user_message})
    
    print(f"--- Query Expansion Node ---")
    print(f"Original: {user_message}")
    print(f"Expanded: {expanded_queries}")
    
    # 元の質問と拡張された質問の両方を状態に保存
    return {
        "original_query": user_message,
        "expanded_queries": [user_message] + expanded_queries
    }

# 拡張されたクエリでRAG検索を実行するノード
def multi_rag_retrieval_node(state: GraphState) -> dict:
    """拡張された各クエリでRAG検索を実行し、結果を統合してコンテキストを作成する。"""
    print(f"--- Multi RAG Retrieval Node ---")
    all_retrieved_docs = []
    
    for query in state["expanded_queries"]:
        # rag/retriever.pyのquery_rag関数
        retrieved_docs = retriever.query_rag_and_get_docs(
            query=query, 
            language=state["language"]
        )
        all_retrieved_docs.extend(retrieved_docs)
    
    # 重複するドキュメントを内容で除去
    unique_docs = {doc.page_content: doc for doc in all_retrieved_docs}.values()
    
    print(f"Retrieved {len(unique_docs)} unique documents.")
    
    # 後のAgentが使いやすいように、辞書のリストとして保存
    context_docs = [{"source": doc.metadata.get('source'), "content": doc.page_content} for doc in unique_docs]
    
    return {"context_documents": context_docs}

def agent_node(state: GraphState) -> dict:
    """
    Agentを実行し、次のアクション（ツール呼び出し or ユーザーへの最終応答）を決定する。
    このノードはツールの「実行はしない」。どのツールを呼ぶか決定するだけ。
    """
    print("--- 1. Agent Node: Deciding next action ---")
    
    # Agentを実行して、LLMに次のアクションを決定させる
    result = agent_executor.invoke({"messages": state["messages"]})

    # Agentがユーザーへの最終応答を生成した場合
    if "output" in result:
        print("Agent decided to respond to user.")
        return {"messages": [AIMessage(content=result["output"])]}
    
    # Agentがツールを呼び出すことを決定した場合
    # tool_calls属性は LangChain v0.2.x の標準的な出力
    if "tool_calls" in result and result["tool_calls"]:
        print(f"Agent wants to call tools: {[tc['name'] for tc in result['tool_calls']]}")
        # 次のノード(tool_executor_node)が実行できるように、tool_callsをメッセージリストに追加して返す
        return {"messages": [AIMessage(content="", tool_calls=result["tool_calls"])]}

    # 想定外の形式の場合は、エラーとして応答する
    print("Agent returned unexpected format.")
    return {"messages": [AIMessage(content="申し訳ありません、予期せぬエラーが発生しました。")]}

def tools_node(state: GraphState) -> dict:
    """
    ツールを実行し、その結果をメッセージリストに追加するノード。
    （注：上記のagent_node内でツール実行まで完結させたため、このノードは呼ばれなくてもよい）
    """
    # このサンプルではagent_nodeでツール実行まで行うため、このノードはシンプルに
    print("--- 2. Tools Node ---")
    # 何もせず、状態をそのまま返す
    return {}

def tool_executor_node(state: GraphState) -> dict:
    """
    Agentが決定したツールを「実際に実行」するノード。
    ★重要：ツールを実行する直前に、グラフの状態から追加の引数を注入する。
    """
    print("--- 2. Tool Executor Node: Running tools ---")
    
    # 最後のメッセージにツール呼び出し情報が含まれているか確認
    last_message = state['messages'][-1]
    if not hasattr(last_message, 'tool_calls') or not last_message.tool_calls:
        # ツール呼び出しがない場合は、何もせず終了
        return {}

    tool_messages = []
    for tool_call in last_message.tool_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]
        print(f"Executing tool: {tool_name} with args: {tool_args}")

        # ★★★ ここが今回の修正の核心 ★★★
        # もし呼び出すツールが 'calculate_route' で、
        # かつ出発地が「現在地」の場合...
        if tool_name == "calculate_route" and tool_args.get("start_point") == "現在地":
            print("Injecting 'current_location' into calculate_route arguments...")
            # グラフの状態(state)から現在地情報を取得し、ツールの引数に追加する
            tool_args["current_location"] = state.get("current_location")
            print(f"Updated args: {tool_args}")
        
        # ツール名で利用可能なツールリストから実行する関数を検索
        tool_to_call = next((t for t in available_tools if t.name == tool_name), None)
        
        if tool_to_call:
            # 準備した引数でツールを実行し、その結果をToolMessageとして保存
            try:
                tool_output = tool_to_call.invoke(tool_args)
                tool_messages.append(
                    ToolMessage(content=json.dumps(tool_output, ensure_ascii=False), tool_call_id=tool_call['id'])
                )
            except Exception as e:
                print(f"Error executing tool {tool_name}: {e}")
                tool_messages.append(
                    ToolMessage(content=f"ツールの実行中にエラーが発生しました: {e}", tool_call_id=tool_call['id'])
                )
        else:
            print(f"Error: Tool '{tool_name}' not found.")
            tool_messages.append(
                ToolMessage(content=f"ツール '{tool_name}' が見つかりませんでした。", tool_call_id=tool_call['id'])
            )

    return {"messages": tool_messages}

def classify_intent_node(state: GraphState) -> dict:
    """ユーザーのメッセージから意図を分類する。"""
    user_message = state["messages"][-1].content
    
    prompt_template = ChatPromptTemplate.from_template(
        """ユーザーのメッセージを分析し、以下の意図のうち最も適切なものを一つだけ選んでください。
        - greeting: 単純な挨拶や雑談
        - general_question: ルートや訪問計画以外の一般的な質問
        - route_request: ルートの提案を求める要求
        - plan_visit_request: 訪問計画に関する要求

        ユーザーメッセージ: "{message}"
        意図: """
        )
    
    # LLMを使って意図を分類する小さなチェーン
    classification_chain = prompt_template | llm | StrOutputParser()
    intent = classification_chain.invoke({"message": user_message})
    
    # 想定される意図に変換
    if "greeting" in intent:
        classified_intent = "greeting"
    elif "general_question" in intent:
        classified_intent = "general_question"
    elif "route_request" in intent:
        classified_intent = "route_request"
    elif "plan_visit_request" in intent:
        classified_intent = "plan_visit_request"
    else:
        classified_intent = "general_question" # 不明な場合は汎用的な質問として扱う

    return {"intent": classified_intent}

def classify_confirmation_node(state: GraphState) -> dict:
    """ルート提案に対するユーザーの応答が肯定的か否定的かを分類する。"""
    user_message = state["messages"][-1].content
    prompt_template = ChatPromptTemplate.from_template(
        "ユーザーの応答が、提案に対して「肯定的」か「否定的」かを判断してください。\n"
        "'affirmative' または 'negative' のどちらか一つだけを返してください。\n\n"
        "ユーザーの応答: \"{message}\"\n判断: "
    )
    classification_chain = prompt_template | llm | StrOutputParser()
    confirmation = classification_chain.invoke({"message": user_message})
    return {"intent": "affirmative" if "affirmative" in confirmation.lower() else "negative"}

def propose_route_node(state: GraphState) -> dict:
    """ツールが計算したルート情報を基に、ユーザーへの確認メッセージを生成し、状態を更新する。"""
    # 文字列を辞書に変換
    try:
        # 最後のToolMessageから`calculate_route`ツールの結果を取得
        tool_output_str = state["messages"][-1].content
        route_info = ast.literal_eval(tool_output_str)
        summary = route_info.get("summary", "詳細不明なルート")
    except (ValueError, SyntaxError):
        summary = "ルート情報の取得に失敗しました。"

    response_message = f"{summary}\nこちらのルートでご案内してもよろしいですか？"
    
    return {
        "messages": [AIMessage(content=response_message)],
        "task_status": "confirming_route" # 状態を「確認中」に更新
    }

def start_guidance_node(state: GraphState) -> dict:
    """ルート案内にユーザーが合意した際の開始メッセージを生成し、状態を更新する。"""
    response_message = "承知しました。では、案内を開始します。準備はよろしいですか？まずは地図に表示された出発点まで移動してください。"
    return {
        "messages": [AIMessage(content=response_message)],
        "task_status": "guiding"
    }
    
def handle_rejection_node(state: GraphState) -> dict:
    """ユーザーが提案を拒否した際の応答を生成する。"""
    response_message = "承知しました。何か他にお手伝いできることはありますか？別のルートを探しますか？"
    return {
        "messages": [AIMessage(content=response_message)],
        "task_status": "idle" # 状態を元に戻す
    }

def handle_visit_plan_result_node(state: GraphState) -> dict:
    """`check_and_plan_visit`ツールの結果に応じて応答を生成する。"""
    try:
        tool_output_str = state["messages"][-1].content
        plan_result = ast.literal_eval(tool_output_str)
        status = plan_result.get("status")
        if status == "available":
            response_message = plan_result.get("message", "計画を登録しました。")
        elif status == "congested":
            suggestion = plan_result.get("suggestion", "別の日時をご検討ください。")
            response_message = f"申し訳ありません、ご希望の日時は混雑しています。{suggestion}"
        else:
            response_message = "計画の確認中にエラーが発生しました。"
    except (ValueError, SyntaxError):
        response_message = "計画の確認結果を正しく処理できませんでした。"
        
    return {"messages": [AIMessage(content=response_message)]}

def generate_simple_response_node(state: GraphState) -> dict:
    """雑談や簡単な質問に対する応答を生成する。"""
    # このノードではツールを使わずに純粋な応答を生成する
    # 雑談用の簡易的なプロンプト
    simple_prompt = ChatPromptTemplate.from_messages([
        ("system", "あなたは「鳥海山ガイドAI」、ベテランの山岳ガイドです。ユーザーの安全を第一に考え、常に丁寧で、正確かつ分かりやすい情報を提供してください。専門的な質問だけでなく、ユーザーとの親しみやすい会話も楽しんでください。"),
        MessagesPlaceholder(variable_name="messages")
    ])
    
    simple_chain = simple_prompt | llm
    response = simple_chain.invoke({"messages": state["messages"]})
    
    return {"messages": [response]}