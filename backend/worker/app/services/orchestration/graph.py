# worker/app/services/orchestration/graph.py

from langgraph.graph import StateGraph, END
from shared.app.schemas import AgentState

# 各ファイルからノードとルーターをインポート
from .router import route_conversation
from .nodes.shared_nodes import chitchat_node, error_node
from .nodes.information_nodes import search_spot_node, generate_spot_response_node
from .nodes.itinerary_nodes import create_plan_node, summarize_plan_node

# グラフの定義
workflow = StateGraph(AgentState)

# ノードの追加
workflow.add_node("chitchat", chitchat_node)
workflow.add_node("error", error_node)
workflow.add_node("search_spot", search_spot_node)
workflow.add_node("generate_spot_response", generate_spot_response_node)
workflow.add_node("create_plan", create_plan_node)
workflow.add_node("summarize_plan", summarize_plan_node)

# エントリーポイントの設定
# 最初に必ずルーターを通過する
workflow.set_entry_point("router")

# 条件分岐エッジの追加
# ルーターの返す文字列に応じて、次に実行するノードを決定する
workflow.add_conditional_edges(
    "router",
    route_conversation,
    {
        "chitchat_node": "chitchat",
        "search_spot_node": "search_spot",
        "create_plan_node": "create_plan",
        "summarize_plan_node": "summarize_plan",
        "__error__": "error", # ルーターがどのノードも返さなかった場合
    },
)

# 通常のエッジの追加
# 特定のノードの後は、必ず決まったノードに遷移する
workflow.add_edge("search_spot", "generate_spot_response")

# 処理の終点
# 応答が生成されたらENDに遷移し、グラフの実行を終了する
workflow.add_edge("chitchat", END)
workflow.add_edge("error", END)
workflow.add_edge("generate_spot_response", END)
workflow.add_edge("create_plan", END)
workflow.add_edge("summarize_plan", END)


# グラフのコンパイル
# これで実行可能なアプリケーションが完成
app = workflow.compile()

# グラフの可視化（デバッグ用）
# from IPython.display import Image
# Image(app.get_graph().draw_png())