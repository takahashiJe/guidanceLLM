# backend/api_gateway/app/api/v1/plans.py
# 目的：GET /api/v1/plans/{id}/summary を本実装し、最新の route_geojson / plan_version を返却

from __future__ import annotations
from typing import Optional, List, Dict, Any
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session as OrmSession, joinedload

from api_gateway.app.security import get_current_user_optional
from shared.app.database import get_db
from shared.app.models import Plan, Stop, Spot
from shared.app.schemas import PlanSummaryResponse, PlanSummaryStop

router = APIRouter(prefix="/plans", tags=["plans"])

def _collect_totals(route_geojson: Optional[dict]) -> tuple[Optional[float], Optional[float]]:
    if not route_geojson:
        return None, None
    props = route_geojson.get("properties") or {}
    dist_m = props.get("distance_m")
    dur_s = props.get("duration_s")
    if dist_m is not None and dur_s is not None:
        return float(dist_m)/1000.0, float(dur_s)/60.0
    # features 側にあれば集計
    total_m = 0.0; total_s = 0.0; got = False
    for f in route_geojson.get("features") or []:
        p = f.get("properties") or {}
        if "distance_m" in p:
            total_m += float(p["distance_m"]); got = True
        if "duration_s" in p:
            total_s += float(p["duration_s"]); got = True
    if got:
        return (total_m/1000.0 if total_m else None, total_s/60.0 if total_s else None)
    return None, None

@router.get("/{plan_id}/summary", response_model=PlanSummaryResponse)
def get_plan_summary(
    plan_id: int,
    db: OrmSession = Depends(get_db),
    user = Depends(get_current_user_optional),
):
    plan: Plan | None = db.query(Plan).options(joinedload(Plan.stops).joinedload(Stop.spot)).filter(Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="plan not found")
    if user and plan.user_id and user.id != plan.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    stops: List[PlanSummaryStop] = []
    for st in plan.stops:
        sp = st.spot
        stops.append(PlanSummaryStop(
            stop_id=st.id,
            order_index=st.order_index,
            spot_id=sp.id if sp else -1,
            name=(sp.official_name if sp else None),
            lat=(sp.latitude if sp else None),
            lon=(sp.longitude if sp else None),
        ))

    distance_km, duration_min = _collect_totals(plan.route_geojson)

    return PlanSummaryResponse(
        plan_id=plan.id,
        plan_version=plan.route_version or 1,
        route_geojson=plan.route_geojson,
        route_updated_at=plan.route_updated_at,
        stops=stops,
        distance_km=distance_km,
        duration_min=duration_min,
    )
