# worker/app/services/orchestration/graph.py

from langgraph.graph import StateGraph, END
from typing import Literal

from backend.shared.app.schemas import AgentState

# 各ファイルからノードとルーター、そして条件分岐用の関数をインポート
from .router import route_conversation
from .nodes.shared_nodes import chitchat_node, error_node
from .nodes.information_nodes import (
    find_candidate_spots_node,
    gather_nudge_data_node,
    select_best_spot_node,
    generate_nudge_proposal_node,
    check_spot_found, # 条件分岐用の関数
    handle_no_spot_found_node
)
from .nodes.itinerary_nodes import (
    create_plan_node,
    summarize_plan_node,
    extract_plan_edit_node,
    execute_plan_edit_node,
    check_plan_edit_extraction # 条件分岐用の関数
)

# --- グラフの定義 ---
workflow = StateGraph(AgentState)

# --- 全ノードの登録 ---
# Shared
workflow.add_node("chitchat", chitchat_node)
workflow.add_node("error", error_node)

# Information (Nudge) Flow
workflow.add_node("find_candidate_spots", find_candidate_spots_node)
workflow.add_node("gather_nudge_data", gather_nudge_data_node)
workflow.add_node("select_best_spot", select_best_spot_node)
workflow.add_node("generate_nudge_proposal", generate_nudge_proposal_node)
workflow.add_node("handle_no_spot_found", handle_no_spot_found_node)

# Itinerary (Planning) Flow
workflow.add_node("create_plan", create_plan_node)
workflow.add_node("summarize_plan", summarize_plan_node)
workflow.add_node("extract_plan_edit", extract_plan_edit_node)
workflow.add_node("execute_plan_edit", execute_plan_edit_node)


# --- グラフのエントリーポイントとメインルーター ---
workflow.set_entry_point("router")

# メインの条件分岐: ユーザーの意図に応じて各フローの入り口へ
workflow.add_conditional_edges(
    "router",
    route_conversation,
    {
        "chitchat": "chitchat",
        "find_candidate_spots": "find_candidate_spots", # 情報提供フローへ
        "create_plan": "create_plan",                 # 新規計画作成フローへ
        "extract_plan_edit": "extract_plan_edit",     # 計画編集フローへ
        "summarize_plan": "summarize_plan",           # 計画要約フローへ
        "error": "error",
        END: END # 会話を終了する場合
    },
)

# --- 情報提供 (育成型ナッジ) フローの配線 ---
# 1. 候補スポットを検索
workflow.add_edge("find_candidate_spots", "check_spot_found")
# 2. 検索結果に応じて分岐
workflow.add_conditional_edges(
    "check_spot_found",
    check_spot_found,
    {
        "continue": "gather_nudge_data", # 見つかった場合 -> ナッジ情報収集へ
        "stop": "handle_no_spot_found"   # 見つからなかった場合 -> 専用の応答生成へ
    }
)
# 3. ナッジ情報を収集 -> 最適スポットを選択 -> 提案文を生成
workflow.add_edge("gather_nudge_data", "select_best_spot")
workflow.add_edge("select_best_spot", "generate_nudge_proposal")


# --- 周遊計画フローの配線 ---
# 1. 新規計画を作成した後は、必ず計画を要約してユーザーに提示
workflow.add_edge("create_plan", "summarize_plan")

# 2. 計画編集の指示を抽出
workflow.add_edge("extract_plan_edit", "check_plan_edit_extraction")
# 3. 抽出結果に応じて分岐
workflow.add_conditional_edges(
    "check_plan_edit_extraction",
    check_plan_edit_extraction,
    {
        "success": "execute_plan_edit", # 抽出成功 -> 編集実行へ
        "failure": "summarize_plan"     # 抽出失敗 -> 現在の計画を要約して聞き直す
    }
)
# 4. 計画編集を実行した後は、必ず計画を要約してユーザーに結果を報告
workflow.add_edge("execute_plan_edit", "summarize_plan")


# --- 全ての終点ノード ---
# これらのノードが実行されたら、グラフの実行は終了
workflow.add_edge("chitchat", END)
workflow.add_edge("error", END)
workflow.add_edge("generate_nudge_proposal", END)
workflow.add_edge("handle_no_spot_found", END)
workflow.add_edge("summarize_plan", END)


# --- グラフのコンパイル ---
app = workflow.compile()
