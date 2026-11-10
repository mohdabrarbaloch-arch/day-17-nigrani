"""Alert inbox and fleet summary."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import engine
from ..db import get_db
from ..models import Alert, Machine, Reading, User
from ..schemas import AlertOut, FleetSummary, HealthOut, ResolveAlertRequest
from ..security import current_user
from .machines import _health_out

router = APIRouter(prefix="/api", tags=["alerts"])


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(
    open_only: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[Alert]:
    stmt = (
        select(Alert)
        .join(Machine, Machine.id == Alert.machine_id)
        .where(Machine.owner_id == user.id)
        .order_by(Alert.raised_at.desc())
        .limit(limit)
    )
    if open_only:
        stmt = stmt.where(Alert.resolved_at.is_(None))
    return list(db.scalars(stmt))


@router.post("/alerts/{alert_id}/resolve", response_model=AlertOut)
def resolve_alert(
    alert_id: int,
    payload: ResolveAlertRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Alert:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    machine = db.get(Machine, alert.machine_id)
    if machine is None or machine.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    if alert.resolved_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Alert is already resolved")

    alert.resolved_at = datetime.now(UTC)
    alert.resolved_note = payload.note
    db.commit()
    db.refresh(alert)
    return alert


@router.get("/fleet", response_model=FleetSummary)
def fleet(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> FleetSummary:
    """Every machine with its current verdict, plus the counts the header needs."""
    machines = list(
        db.scalars(
            select(Machine)
            .where(Machine.owner_id == user.id, Machine.is_active.is_(True))
            .order_by(Machine.asset_tag)
        )
    )

    healths: list[HealthOut] = []
    for machine in machines:
        readings = list(
            db.scalars(
                select(Reading)
                .where(Reading.machine_id == machine.id)
                .order_by(Reading.recorded_at.desc())
                .limit(engine.TREND_WINDOW)
            )
        )
        readings.reverse()
        healths.append(_health_out(machine, readings))

    counts = {"healthy": 0, "warning": 0, "critical": 0, "emergency": 0}
    for h in healths:
        counts[h.severity or "healthy"] += 1

    open_alerts = db.scalar(
        select(func.count(Alert.id))
        .join(Machine, Machine.id == Alert.machine_id)
        .where(Machine.owner_id == user.id, Alert.resolved_at.is_(None))
    )

    return FleetSummary(
        total=len(healths),
        healthy=counts["healthy"],
        warning=counts["warning"],
        critical=counts["critical"],
        emergency=counts["emergency"],
        open_alerts=int(open_alerts or 0),
        machines=healths,
    )
