# worker/app/services/itinerary/itinerary_service.py

from __future__ import annotations

import logging
from typing import Iterable, List, Optional, Sequence, Tuple, Type, Any

logger = logging.getLogger(__name__)


class ItineraryService:
    """
    旅程（Plan / Stop）を扱うサービス層。
    - SQLAlchemy の ORM モデル（Plan, Stop, Spot）を利用
    - import 時に副作用を出さない（DB/ORM は関数内 import）
    - itinerary_nodes.py から参照される get_plan_details を中心に、
      基本的な CRUD 操作を提供する
    """

    # ---- 内部ユーティリティ -------------------------------------------------

    @staticmethod
    def _import_sqla() -> Tuple[Any, Any, Any]:
        """
        SQLAlchemy 関連を関数内 import（import 時の依存エラー回避）。
        Returns: (sa, orm, select)
        """
        import sqlalchemy as sa
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload, joinedload  # noqa: F401 (型ヒント用/候補で使用)

        return sa, select, selectinload

    @staticmethod
    def _import_models():
        """
        shared.app.models を読み込み、想定名称に対応するモデルを動的に解決する。
        プロジェクト差を吸収するため、複数候補名をフォールバックで探索。
        戻り値: (PlanModel, StopModel, SpotModel, UserModel or None)
        """
        try:
            from shared.app import models  # type: ignore
        except Exception as e:
            raise RuntimeError("shared.app.models の import に失敗しました") from e

        def resolve(*candidates: str) -> Optional[Type[Any]]:
            for name in candidates:
                m = getattr(models, name, None)
                if m is not None:
                    return m
            return None

        PlanModel = resolve("ItineraryPlan", "Plan", "TripPlan", "RoutePlan")
        StopModel = resolve("ItineraryStop", "PlanStop", "TripStop", "RouteStop")
        SpotModel = resolve("Spot")
        UserModel = resolve("User", "AppUser")

        missing = []
        if PlanModel is None:
            missing.append("Plan(ItineraryPlan/Plan/TripPlan/RoutePlan)")
        if StopModel is None:
            missing.append("Stop(ItineraryStop/PlanStop/TripStop/RouteStop)")
        if SpotModel is None:
            missing.append("Spot")

        if missing:
            raise RuntimeError(
                "ItineraryService: 必須モデルが見つかりません: " + ", ".join(missing)
            )

        return PlanModel, StopModel, SpotModel, UserModel

    @staticmethod
    def _ensure_session(db) -> None:
        """
        db が SQLAlchemy の Session/Session-like か最低限チェック。
        """
        # ここでは厳格な isinstance チェックは避け、最低限の属性存在を確認
        need = ("add", "commit", "refresh", "execute")
        for attr in need:
            if not hasattr(db, attr):
                raise TypeError(f"db は SQLAlchemy Session を想定しています（欠落属性: {attr}）")

    # ---- 取得系 -------------------------------------------------------------

    @staticmethod
    def get_plan_details(db, plan_id: int) -> Any:
        """
        プラン詳細を取得して返す。stops は position 昇順でアクセスできるようにする。
        itinerary_nodes.py 側で plan.stops を前提としているため、ORM オブジェクトをそのまま返す。

        Args:
            db: SQLAlchemy Session
            plan_id: 取得対象のプラン ID

        Returns:
            Plan ORM オブジェクト（stops/spot がロード済）
        """
        ItineraryService._ensure_session(db)
        sa, select, selectinload = ItineraryService._import_sqla()
        Plan, Stop, Spot, _User = ItineraryService._import_models()

        # stops と spot を selectinload（または joinedload）でロード
        try:
            # まず selectinload を試す（大量件数に強い）
            stmt = (
                select(Plan)
                .where(Plan.id == plan_id)
                .options(
                    selectinload(Plan.stops).selectinload(Stop.spot)
                )
            )
            result = db.execute(stmt).scalars().first()
        except Exception:
            # environments により options で失敗するケースがあれば joinedload にフォールバック
            from sqlalchemy.orm import joinedload  # type: ignore

            stmt = (
                select(Plan)
                .where(Plan.id == plan_id)
                .options(
                    joinedload(Plan.stops).joinedload(Stop.spot)
                )
            )
            result = db.execute(stmt).scalars().first()

        if result is None:
            raise ValueError(f"指定された plan_id={plan_id} が見つかりません。")

        # stops を position 昇順に並べ替え（DB 側の order_by が未設定の場合に備える）
        if hasattr(result, "stops") and isinstance(result.stops, (list, tuple)):
            try:
                result.stops.sort(key=lambda s: getattr(s, "position", 0))
            except Exception:
                # ソート不可でも致命的ではないためログのみに留める
                logger.debug("plan.stops の並び替えに失敗しました（position 未定義の可能性）")

        return result

    # ---- 生成・編集系（将来的/他ノードでの利用を考慮し用意） -------------

    @staticmethod
    def create_plan(
        db,
        user_id: Optional[int],
        spot_ids: Sequence[int],
        title: Optional[str] = None,
        travel_date: Optional[Any] = None,
    ) -> Any:
        """
        プランを新規作成し、指定 spot_id 群を stops として追加して返す。
        """
        ItineraryService._ensure_session(db)
        Plan, Stop, Spot, _User = ItineraryService._import_models()

        plan = Plan()
        if title is not None and hasattr(plan, "title"):
            setattr(plan, "title", title)
        if travel_date is not None and hasattr(plan, "travel_date"):
            setattr(plan, "travel_date", travel_date)
        if user_id is not None and hasattr(plan, "user_id"):
            setattr(plan, "user_id", user_id)

        db.add(plan)
        db.commit()
        db.refresh(plan)

        # stops 追加
        position = 1
        for sid in spot_ids:
            stop = Stop()
            # 外部キー
            if hasattr(stop, "plan_id"):
                setattr(stop, "plan_id", plan.id)
            if hasattr(stop, "spot_id"):
                setattr(stop, "spot_id", sid)
            # 表示順
            if hasattr(stop, "position"):
                setattr(stop, "position", position)
            position += 1
            db.add(stop)

        db.commit()
        db.refresh(plan)

        # 返却時に stops をロード/整列
        return ItineraryService.get_plan_details(db, plan.id)

    @staticmethod
    def reorder_stops(db, plan_id: int, ordered_stop_ids: Sequence[int]) -> Any:
        """
        stops の並び順を stop_id の並びに合わせて更新。
        """
        ItineraryService._ensure_session(db)
        Plan, Stop, Spot, _User = ItineraryService._import_models()

        plan = ItineraryService.get_plan_details(db, plan_id)
        # 既存 stop を id -> obj で引く
        stop_map = {getattr(s, "id"): s for s in getattr(plan, "stops", [])}

        pos = 1
        for sid in ordered_stop_ids:
            s = stop_map.get(sid)
            if s is None:
                continue
            if hasattr(s, "position"):
                setattr(s, "position", pos)
            pos += 1

        db.commit()
        return ItineraryService.get_plan_details(db, plan_id)

    @staticmethod
    def add_stop(
        db,
        plan_id: int,
        spot_id: int,
        position: Optional[int] = None,
    ) -> Any:
        """
        指定プランに stop を 1 件追加。position を省略した場合は末尾に追加。
        """
        ItineraryService._ensure_session(db)
        Plan, Stop, Spot, _User = ItineraryService._import_models()

        plan = ItineraryService.get_plan_details(db, plan_id)
        # 既存 stops の末尾+1 を既定 position に
        max_pos = 0
        for s in getattr(plan, "stops", []):
            p = getattr(s, "position", 0)
            if isinstance(p, int) and p > max_pos:
                max_pos = p
        if position is None or not isinstance(position, int) or position <= 0:
            position = max_pos + 1

        stop = Stop()
        if hasattr(stop, "plan_id"):
            setattr(stop, "plan_id", plan_id)
        if hasattr(stop, "spot_id"):
            setattr(stop, "spot_id", spot_id)
        if hasattr(stop, "position"):
            setattr(stop, "position", position)

        db.add(stop)
        db.commit()
        return ItineraryService.get_plan_details(db, plan_id)

    @staticmethod
    def remove_stop(db, plan_id: int, stop_id: int) -> Any:
        """
        指定プランから stop を 1 件削除（物理削除）。論理削除カラムがある場合はそれを優先。
        """
        ItineraryService._ensure_session(db)
        Plan, Stop, Spot, _User = ItineraryService._import_models()

        plan = ItineraryService.get_plan_details(db, plan_id)
        target = None
        for s in getattr(plan, "stops", []):
            if getattr(s, "id", None) == stop_id:
                target = s
                break
        if target is None:
            raise ValueError(f"plan_id={plan_id} に stop_id={stop_id} は存在しません。")

        # 論理削除が定義されていればそれを使う（deleted 等）
        if hasattr(target, "deleted"):
            setattr(target, "deleted", True)
        else:
            # 物理削除
            db.delete(target)

        db.commit()
        return ItineraryService.get_plan_details(db, plan_id)
