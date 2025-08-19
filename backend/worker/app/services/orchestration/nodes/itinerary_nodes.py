# /app/backend/worker/app/services/orchestration/nodes/itinerary_nodes.py

# =================================================================
# === 変更点①: 必要なモジュールを追加 =============================
# =================================================================
from datetime import date
from typing import Any, Callable, Dict, List

from langchain_core.messages import ToolMessage

from shared.app.database import SessionLocal
from worker.app.services.itinerary import itinerary_service
from worker.app.services.orchestration.state import AgentState


def _get_tool_call_param(tool_call: Dict[str, Any], param_name: str, default: Any = None) -> Any:
    """ツールコールの引数を安全に取得するヘルパー"""
    return tool_call.get("args", {}).get(param_name, default)


# =================================================================
# === 変更点②: 各CRUDノードを内部処理用のハンドラ関数に変更 ===========
# =================================================================
# - 関数名を `_handle_...` に変更
# - AgentStateに加え、具体的な tool_call 辞書を引数で受け取るように変更

def _handle_create_plan(state: AgentState, tool_call: Dict[str, Any]) -> Dict[str, Any]:
    """'create_plan' ツールコールを処理する"""
    user_id = state["user_id"]
    session_id = state["session_id"]
    title = _get_tool_call_param(tool_call, "title", "新しい周遊計画")
    start_date_str = _get_tool_call_param(tool_call, "start_date")
    start_date = date.fromisoformat(start_date_str) if start_date_str else date.today()

    with SessionLocal() as db:
        plan_summary = itinerary_service.create_plan_for_user(
            db, user_id=user_id, session_id=session_id, title=title, start_date=start_date
        )

    tool_message = ToolMessage(
        content=f"新しい周遊計画を作成しました。計画IDは {plan_summary['plan_id']} です。",
        tool_call_id=tool_call["id"],
    )
    return {"plan": plan_summary, "messages": [tool_message]}


def _handle_add_spot(state: AgentState, tool_call: Dict[str, Any]) -> Dict[str, Any]:
    """'add_spot_to_plan' ツールコールを処理する"""
    plan_id = state.get("plan", {}).get("plan_id")
    if not plan_id:
        raise ValueError("計画がアクティブではありません。")
    spot_id = _get_tool_call_param(tool_call, "spot_id")
    position = _get_tool_call_param(tool_call, "position")
    if not spot_id:
         raise ValueError("spot_idが指定されていません。")

    with SessionLocal() as db:
        updated_plan_summary = itinerary_service.add_spot_to_user_plan(
            db, plan_id=plan_id, spot_id=spot_id, position=position
        )

    tool_message = ToolMessage(
        content=f"計画にスポット(ID: {spot_id})を追加しました。",
        tool_call_id=tool_call["id"],
    )
    return {"plan": updated_plan_summary, "messages": [tool_message]}


def _handle_remove_spot(state: AgentState, tool_call: Dict[str, Any]) -> Dict[str, Any]:
    """'remove_spot_from_plan' ツールコールを処理する"""
    plan_id = state.get("plan", {}).get("plan_id")
    if not plan_id:
        raise ValueError("計画がアクティブではありません。")
    spot_id = _get_tool_call_param(tool_call, "spot_id")
    if not spot_id:
         raise ValueError("spot_idが指定されていません。")

    with SessionLocal() as db:
        updated_plan_summary = itinerary_service.remove_spot_from_user_plan(
            db, plan_id=plan_id, spot_id=spot_id
        )

    tool_message = ToolMessage(
        content=f"計画からスポット(ID: {spot_id})を削除しました。",
        tool_call_id=tool_call["id"],
    )
    return {"plan": updated_plan_summary, "messages": [tool_message]}


def _handle_reorder_stops(state: AgentState, tool_call: Dict[str, Any]) -> Dict[str, Any]:
    """'reorder_plan_stops' ツールコールを処理する"""
    plan_id = state.get("plan", {}).get("plan_id")
    if not plan_id:
        raise ValueError("計画がアクティブではありません。")
    spot_ids_in_order = _get_tool_call_param(tool_call, "spot_ids_in_order")
    if not isinstance(spot_ids_in_order, list) or not spot_ids_in_order:
        raise ValueError("spot_ids_in_orderがリスト形式で正しく指定されていません。")

    with SessionLocal() as db:
        updated_plan_summary = itinerary_service.reorder_user_plan_stops(
            db, plan_id=plan_id, spot_ids_in_order=spot_ids_in_order
        )

    tool_message = ToolMessage(
        content=f"計画の訪問順を更新しました。",
        tool_call_id=tool_call["id"],
    )
    return {"plan": updated_plan_summary, "messages": [tool_message]}


# =================================================================
# === 変更点③: ディスパッチャー用のノードをここから追記 ================
# =================================================================

# --- ツール名と処理ハンドラの対応表 ---
_TOOL_DISPATCHER: Dict[str, Callable[[AgentState, Dict], Dict[str, Any]]] = {
    "create_plan": _handle_create_plan,
    "add_spot_to_plan": _handle_add_spot,
    "remove_spot_from_plan": _handle_remove_spot,
    "reorder_plan_stops": _handle_reorder_stops,
}


def upsert_plan_node(state: AgentState) -> Dict[str, Any]:
    """
    周遊計画のCRUD操作をツールコールに応じて振り分けるノード。
    graph.pyからはこのノードが呼び出される。
    """
    # 1. AgentStateから最新のツールコールを取得
    last_message = state["messages"][-1]
    if not last_message.tool_calls:
        raise ValueError("upsert_plan_nodeが呼び出されましたが、ツールコールが存在しません。")
    tool_call = last_message.tool_calls[0]
    tool_name = tool_call["name"]

    # 2. ディスパッチャーを使って適切なハンドラを呼び出す
    handler = _TOOL_DISPATCHER.get(tool_name)
    if not handler:
        raise NotImplementedError(f"ツール '{tool_name}' に対応する処理が実装されていません。")

    # 3. 選択されたハンドラを実行してAgentStateを更新
    return handler(state, tool_call)


# =================================================================
# === 変更点④: summarize_plan_node をリネーム ======================
# =================================================================

def calc_preview_route_and_summarize_node(state: AgentState) -> Dict[str, Any]:
    """
    計画の現在の状態を要約し、プレビュー用のルート情報を計算するノード。
    (旧: summarize_plan_node)
    """
    plan_id = state.get("plan", {}).get("plan_id")
    if not plan_id:
        # 計画が存在しない場合はサマリーも不要なので何もしない
        return {}

    with SessionLocal() as db:
        # serviceのsummarize_planはルート計算も内包している
        plan_summary = itinerary_service.summarize_plan(db, plan_id=plan_id)

    # LLMに現在の計画を自然言語で説明させるためのコンテキストを生成
    stop_names = [s['name'] for s in plan_summary.get('stops', [])]
    summary_text = f"現在の計画は「{plan_summary.get('title')}」です。訪問地は {', '.join(stop_names)} が含まれています。"
    if plan_summary.get("total_duration_minutes", 0) > 0:
        duration = plan_summary['total_duration_minutes']
        summary_text += f" 全体の移動時間は約{duration}分と計算されました。"
    else:
        summary_text += " 訪問地が1つだけなので、移動時間はまだ計算されていません。"

    # このサマリーはLLMへの次の入力となるため、plan stateに含める
    plan_summary['llm_summary_context'] = summary_text

    return {"plan": plan_summary}