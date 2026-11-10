"""Alerts and work orders."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Alert, AutomationLog, Machine, User, WorkOrder, WorkOrderStatus
from app.schemas import (
    AlertOut,
    AutomationLogOut,
    WorkOrderOut,
    WorkOrderUpdate,
)

router = APIRouter(prefix="/api", tags=["operations"])

_VALID_STATUSES = {s.value for s in WorkOrderStatus}


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(
    severity: str | None = None,
    acknowledged: bool | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[AlertOut]:
    stmt = select(Alert).order_by(Alert.created_at.desc())
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    if acknowledged is not None:
        stmt = stmt.where(Alert.acknowledged.is_(acknowledged))
    stmt = stmt.limit(max(1, min(limit, 500)))
    return [AlertOut.model_validate(a) for a in db.scalars(stmt).all()]


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertOut)
def acknowledge_alert(
    alert_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> AlertOut:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    alert.acknowledged = True
    db.commit()
    db.refresh(alert)
    return AlertOut.model_validate(alert)


@router.get("/work-orders", response_model=list[WorkOrderOut])
def list_work_orders(
    status_filter: str | None = None,
    machine_id: int | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[WorkOrderOut]:
    stmt = select(WorkOrder).order_by(WorkOrder.created_at.desc())
    if status_filter:
        stmt = stmt.where(WorkOrder.status == status_filter)
    if machine_id is not None:
        stmt = stmt.where(WorkOrder.machine_id == machine_id)
    stmt = stmt.limit(max(1, min(limit, 500)))
    return [WorkOrderOut.model_validate(w) for w in db.scalars(stmt).all()]


@router.patch("/work-orders/{order_id}", response_model=WorkOrderOut)
def update_work_order(
    order_id: int,
    payload: WorkOrderUpdate,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> WorkOrderOut:
    order = db.get(WorkOrder, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work order not found")

    if payload.status is not None:
        if payload.status not in _VALID_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"status must be one of {sorted(_VALID_STATUSES)}",
            )
        order.status = payload.status
        if payload.status in (WorkOrderStatus.DONE, WorkOrderStatus.CANCELLED):
            order.closed_at = datetime.now(UTC)
            machine = db.get(Machine, order.machine_id)
            if machine is not None and payload.status == WorkOrderStatus.DONE:
                machine.status = "running"

    if payload.assigned_to is not None:
        order.assigned_to = payload.assigned_to

    db.commit()
    db.refresh(order)
    return WorkOrderOut.model_validate(order)


@router.get("/automation-log", response_model=list[AutomationLogOut])
def list_automation_log(
    limit: int = 50,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[AutomationLogOut]:
    logs = db.scalars(
        select(AutomationLog)
        .order_by(AutomationLog.created_at.desc())
        .limit(max(1, min(limit, 500)))
    ).all()
    return [AutomationLogOut.model_validate(entry) for entry in logs]
