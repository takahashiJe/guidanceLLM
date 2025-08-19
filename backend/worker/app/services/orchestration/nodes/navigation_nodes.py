# orchestration/nodes/navigation_nodes.py

from __future__ import annotations
from typing import List, Dict, Any, Optional

from worker.app.services.navigation.navigation_service import NavigationService
from ..state import AgentState

def start_navigation_node(state: AgentState) -> Dict[str, Any]:
    """
    ナビゲーションを開始し、ルート全体の案内テキストを事前生成するノード。

    - AgentStateからactive_plan_idを取得。
    - IDがない場合はエラーメッセージを生成して終了。
    - NavigationServiceを呼び出し、事前生成されたガイドを取得。
    - AgentStateをナビゲーションモードに更新し、結果を格納する。
    """
    print("--- 6.1. ナビゲーション開始ノード ---")
    active_plan_id = state.get("active_plan_id")

    if not active_plan_id:
        # アクティブな計画がない場合、ユーザーに通知して終了
        ai_message = "エラー: ナビゲーションを開始するための有効な周遊計画がありません。"
        state["messages"].append(("ai", ai_message))
        return {
            "messages": state["messages"],
            "is_navigating": False  # 念のためフラグを倒す
        }

    try:
        navigation_service = NavigationService()
        # ナビゲーションサービスを呼び出し、ガイドを事前生成
        guides = navigation_service.start_navigation_session(active_plan_id)

        # 案内開始のメッセージを生成
        ai_message = "ナビゲーションを開始します。目的地に向かって出発してください。道中、現在地に合わせて自動で案内を行います。"
        state["messages"].append(("ai", ai_message))

        # 状態をナビゲーションモードに更新
        return {
            "messages": state["messages"],
            "is_navigating": True,
            "pre_generated_guides": guides
        }
    except Exception as e:
        print(f"ナビゲーション開始時にエラーが発生しました: {e}")
        error_message = f"申し訳ありません、ナビゲーションの準備中にエラーが発生しました。({e})"
        state["messages"].append(("ai", error_message))
        return {
            "messages": state["messages"],
            "is_navigating": False
        }


def end_navigation_node(state: AgentState) -> Dict[str, Any]:
    """
    ナビゲーションを終了し、状態を通常モードに戻すノード。
    """
    print("--- ナビゲーション終了ノード ---")
    
    ai_message = "ナビゲーションを終了しました。通常の対話モードに戻ります。"
    state["messages"].append(("ai", ai_message))
    
    # ナビゲーション関連の状態をリセット
    return {
        "messages": state["messages"],
        "is_navigating": False,
        "pre_generated_guides": None
    }