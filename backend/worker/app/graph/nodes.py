# /backend/app/graph/nodes.py

import os
import ast 
import uuid
import json
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import AIMessage, ToolMessage, HumanMessage
from langchain_core.agents import AgentAction, AgentFinish
from langchain_ollama import ChatOllama
from langchain_core.output_parsers import StrOutputParser, JsonOutputParser
from langchain.agents import create_tool_calling_agent
from datetime import date
import inspect

# --- サービスのインポート ---
# from app.services import planning_service
# from app.db import session as db_session

from shared.state import GraphState
from worker.app.graph.tools import available_tools
from worker.app.rag import retriever
from worker.app.graph.intent_tools import intent_classification_tools

# --- Agentのセットアップ ---
# この部分はアプリケーション起動時に一度だけ実行されるのが望ましい
SYSTEM_PROMPT_TEMPLATE = """あなたは「鳥海山ガイドAI」、ベテランの山岳ガイドです。
ユーザーの安全を第一に考え、常に丁寧で、正確かつ分かりやすい情報を提供してください。

【あなたの情報】
あなたのタスクを管理するため、以下の情報を利用してください。
- 現在のユーザーID: {user_id}

【重要】鳥海山に関する情報（スポット，コース，アクセス，施設，歴史，文化，自然，動植物）に関するあなたの回答は、必ず以下のナレッジベースの情報にのみ基づいて生成してください。
ナレッジベースに情報がない質問については、曖昧な知識で答えず、正直に「申し訳ありません。その情報については私のデータベースに存在せず，お答えできません。」と回答してください。

【現在のユーザーの訪問計画】
{visit_plan_summary}

【ナレッジベースの情報】
{knowledge_base_context} 

【あなたの行動原則】
1.  **訪問計画の管理**: ユーザーから訪問計画に関する依頼（例：「7/15に鳥海湖行きたい」「計画やめる」「来週の空いてる日は？」）を受けたら、`manage_visit_plan`ツールを呼び出してください。ユーザーの意図を正確に解釈し、ツールの`action`と各引数を設定してください。
2.  **混雑状況の伝達**: `manage_visit_plan`ツールの結果に`"is_congested": true`が含まれていたら、「計画を保存しましたが、その日は混雑が予想されます」のように、必ず混雑している旨を伝えてください。
3.  **期間提案**: `manage_visit_plan`ツールの結果に`"congestion_map"`が含まれていたら、その中で値が小さい日付をユーザーに提案してください。例：「7月13日から20日の間では、特に16日と17日が比較的空いているようです。」
4.  **地名正規化とルート計算**: ユーザーからルートに関する質問を受けたら、`normalize_location_names`と`calculate_route`を順に利用してください。
5.  **その他の質問**: 上記以外の場合は、`knowledge_base_search`などの適切なツールを利用してください。
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

def rag_synthesis_node(state: GraphState) -> dict:
    """
    RAGで取得したコンテキスト情報とユーザーの質問を基に、最終的な回答を生成する。
    このノードはツールを呼び出さず、応答生成に特化する。
    """
    print("--- 3. RAG Synthesis Node ---")

    # Stateから整形済みのナレッジベースコンテキストを取得
    plan_info = state.get("visit_plan") 
    if plan_info:
        summary = f"場所: {plan_info['spot_name']}, 日付: {plan_info['visit_date']}"
    else:
        summary = "現在、計画はありません。"

    context_docs = state.get("context_documents", [])
    if context_docs:
        knowledge_base_context = "\n\n---\n\n".join(
            [f"Source: {doc.get('source', 'N/A')}\nContent: {doc.get('content', '')}" for doc in context_docs]
        )
    else:
        knowledge_base_context = "利用可能なナレッジベースの情報はありません。"

    # 応答生成に特化したプロンプトを作成
    synthesis_prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT_TEMPLATE.format(
            user_id=state.get("user_id", "unknown_user"), #追加
            visit_plan_summary=summary,
            knowledge_base_context=knowledge_base_context
        )),
        MessagesPlaceholder(variable_name="messages"),
    ])
    
    # LLMチェーンを構築し、応答を生成
    synthesis_chain = synthesis_prompt | llm | StrOutputParser()
    
    final_answer = synthesis_chain.invoke({"messages": state["messages"]})
    
    return {"messages": [AIMessage(content=final_answer)]}

def agent_node(state: GraphState) -> dict:
    """Agentを実行し、次のアクションを決定する。会話の開始時にDBから計画を読み込む。"""
    print("--- 1. Agent Node: Deciding next action ---")

    # --- 文脈に応じたプロンプトの生成 ---
    # tasks.pyから渡されたStateを直接利用する
    plan_info = state.get("visit_plan") 
    if plan_info:
        summary = f"場所: {plan_info['spot_name']}, 日付: {plan_info['visit_date']}"
    else:
        summary = "現在、計画はありません。"
    
    # 1. Stateからナレッジベースの検索結果を取得
    context_docs = state.get("context_documents", [])
    if context_docs:
        # ドキュメントのリストを文字列に変換してプロンプトに埋め込む
        knowledge_base_context = "\n\n---\n\n".join(
            [f"Source: {doc.get('source', 'N/A')}\nContent: {doc.get('content', '')}" for doc in context_docs]
        )
    else:
        knowledge_base_context = "利用可能なナレッジベースの情報はありません。"
    
    # 2. プロンプトをフォーマットする際に、必要な変数をすべて渡す
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        user_id=state.get("user_id", "unknown_user"),
        visit_plan_summary=summary,
        knowledge_base_context=knowledge_base_context
    )
    
    # 動的に生成したプロンプトでAgentを初期化
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        MessagesPlaceholder(variable_name="messages"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    agent = create_tool_calling_agent(llm, available_tools, prompt)

    messages = state["messages"]
    tool_call_map = {}
    for i in range(len(messages) - 1):
        if (
            isinstance(messages[i], AIMessage)
            and messages[i].tool_calls
            and isinstance(messages[i + 1], ToolMessage)
        ):
            for tool_call in messages[i].tool_calls:
                tool_call_map[tool_call["id"]] = messages[i + 1]

    intermediate_steps = []
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tool_call in msg.tool_calls:
                if tool_call["id"] in tool_call_map:
                    # (AgentAction, ToolMessage.content) のタプルを作成
                    action = AgentAction(
                        tool=tool_call["name"],
                        tool_input=tool_call["args"],
                        log=f"Invoking tool `{tool_call['name']}` with "
                        f"arguments: {tool_call['args']}\n",
                    )
                    intermediate_steps.append((action, tool_call_map[tool_call["id"]].content))
    
    # "messages"と"intermediate_steps"を渡してエージェントを実行
    agent_outcome = agent.invoke({
        "messages": state["messages"],
        "intermediate_steps": intermediate_steps
    })
    
    # (以降のAgentAction, AgentFinishの処理は変更なし)
    actions = []
    if isinstance(agent_outcome, list) and all(isinstance(i, AgentAction) for i in agent_outcome):
        actions = agent_outcome
    elif isinstance(agent_outcome, AgentAction):
        actions = [agent_outcome]

    if actions:
        tool_calls = [{"name": action.tool, "args": action.tool_input, "id": str(uuid.uuid4())} for action in actions]
        ai_message_with_tools = AIMessage(content="", tool_calls=tool_calls)
        return {"messages": [ai_message_with_tools]}

    if isinstance(agent_outcome, AgentFinish):
        return {"messages": [AIMessage(content=agent_outcome.return_values["output"])]}

    raise ValueError(f"Agent returned unexpected type: {type(agent_outcome)}")

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
    """Agentが決定したツールを「実際に実行」するノード。"""
    print("--- 2. Tool Executor Node: Running tools ---")
    
    last_message = state['messages'][-1]
    if not hasattr(last_message, 'tool_calls') or not last_message.tool_calls:
        return {}

    tool_messages = []
    for tool_call in last_message.tool_calls:
        tool_name = tool_call["name"]
        # AIが生成した引数をコピー
        tool_args = tool_call["args"].copy()
        
        tool_to_call = next((t for t in available_tools if t.name == tool_name), None)
        
        if tool_to_call:
            # ツールの引数シグネチャを検査
            tool_signature = inspect.signature(tool_to_call.func)
            
            # Stateに存在する共通引数を自動で注入する
            if 'user_id' in tool_signature.parameters and 'user_id' not in tool_args:
                tool_args['user_id'] = state.get('user_id')
            if 'language' in tool_signature.parameters and 'language' not in tool_args:
                tool_args['language'] = state.get('language')
            
            print(f"Executing tool: {tool_name} with args: {tool_args}")

            try:
                tool_output = tool_to_call.invoke(tool_args)
                tool_messages.append(ToolMessage(content=json.dumps(tool_output, ensure_ascii=False), tool_call_id=tool_call['id']))
            except Exception as e:
                tool_messages.append(ToolMessage(content=f"ツールの実行中にエラーが発生しました: {e}", tool_call_id=tool_call['id']))
        else:
            tool_messages.append(ToolMessage(content=f"ツール '{tool_name}' が見つかりませんでした。", tool_call_id=tool_call['id']))

    return {"messages": tool_messages}

def classify_intent_node(state: GraphState) -> dict:
    """
    ユーザーのメッセージを分析し、LLMのTool-Binding機能を使って意図を決定する。
    """
    print("--- Intent Classification Node (Tool-Binding) ---")
    user_message = state["messages"][-1] # HumanMessageオブジェクトを直接取得

    # 1. LLMに意図選択ツールを直接バインドする
    llm_with_intent_tools = llm.bind_tools(intent_classification_tools)

    # 2. 意図分類のためのシンプルなメッセージリストを作成
    #    システムプロンプトの役割を担う指示メッセージを追加
    system_instruction = "あなたはユーザーのメッセージの意図を分析する専門家です。提示されたツールの中から、ユーザーの意図に最も合致するものを「必ず1つだけ」選択してください。"
    
    messages_for_classification = [
        HumanMessage(content=system_instruction),
        user_message # ユーザーの実際のメッセージ
    ]
    
    # 3. LLMを直接呼び出し、応答を取得
    ai_response = llm_with_intent_tools.invoke(messages_for_classification)

    # 4. LLMの応答にツールコールが含まれているか確認し、意図を決定
    classified_intent = "general_question"  # デフォルト値

    # ai_response.tool_calls に、LLMが呼び出すと判断したツールのリストが入る
    if ai_response.tool_calls:
        # 最初のツールコールの名前を取得
        tool_name = ai_response.tool_calls[0]['name']
        
        if tool_name == "select_route_request_intent":
            classified_intent = "route_request"
        elif tool_name == "select_plan_visit_request_intent":
            classified_intent = "plan_visit_request"
        elif tool_name == "select_greeting_intent":
            classified_intent = "greeting"

    print(f"User Message: {user_message.content}")
    print(f"Classified Intent: {classified_intent}")

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
    """
    manage_visit_planツールの結果を正しく処理し、最終的な応答と状態を生成する。
    """
    try:
        # ツールが返した生のJSON文字列を取得
        tool_output_str = state["messages"][-1].content
        plan_result = json.loads(tool_output_str) # 必ずjson.loads()を使用
        status = plan_result.get("status")

        response_message = "計画を登録しました。"
        final_visit_plan_data: Optional[VisitPlanState] = None

        if status == "saved":
            # "YYYY-MM-DD" 形式の文字列を、date.fromisoformatで直接dateオブジェクトに変換
            visit_date_obj = date.fromisoformat(plan_result.get("visit_date"))

            # GraphStateに保存するための、型が保証されたデータを作成
            final_visit_plan_data = {
                "spot_id": plan_result.get("spot_id"),
                "spot_name": plan_result.get("spot_name"),
                "visit_date": visit_date_obj
            }

            # ユーザーへの応答メッセージを作成
            if plan_result.get("is_congested"):
                response_message = f"{visit_date_obj.strftime('%-m月%-d日')}の「{plan_result.get('spot_name')}」への計画を登録しましたが、当日は混雑が予想されます。別の日も検討しますか？"
            else:
                response_message = f"{visit_date_obj.strftime('%-m月%-d日')}の「{plan_result.get('spot_name')}」への計画を登録しました。"

        elif status in ["invalid_spot", "error", "not_found", "deleted"]:
            # ツールが返したエラーメッセージなどをそのまま応答にする
            response_message = plan_result.get("message", "処理が完了しましたが、メッセージはありません。")

        else:
            # 予期せぬステータスの場合
            response_message = "計画の確認中に予期せぬエラーが発生しました。"

        # 最終的な応答メッセージと、更新された訪問計画を返す
        return {
            "messages": [AIMessage(content=response_message)],
            "visit_plan": final_visit_plan_data
        }

    except (json.JSONDecodeError, ValueError, TypeError, SyntaxError) as e:
        # このブロックでエラーが発生した場合のログ出力
        print(f"---!!! ERROR IN handle_visit_plan_result_node !!!---")
        traceback.print_exc()
        response_message = "計画の確認結果を正しく処理できませんでした。システム管理者にご確認ください。"
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