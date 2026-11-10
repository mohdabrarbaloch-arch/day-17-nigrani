"""Fleet dashboard summary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.engine.health import score_health
from app.models import (
    Alert,
    AutomationLog,
    Machine,
    Reading,
    User,
    WorkOrder,
    WorkOrderStatus,
)
from app.schemas import AutomationLogOut, DashboardSummary

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_OPEN_STATUSES = [WorkOrderStatus.OPEN, WorkOrderStatus.ASSIGNED, WorkOrderStatus.IN_PROGRESS]


@router.get("/summary", response_model=DashboardSummary)
def summary(
    db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> DashboardSummary:
    machines = db.scalars(select(Machine)).all()

    critical = warning = healthy = 0
    health_values: list[float] = []
    worst: list[dict] = []

    for machine in machines:
        latest = db.scalar(
            select(Reading)
            .where(Reading.machine_id == machine.id)
            .order_by(Reading.recorded_at.desc())
            .limit(1)
        )
        if latest is None:
            continue

        result = score_health(
            vibration_rms=latest.vibration_rms,
            temperature_c=latest.temperature_c,
            current_a=latest.current_a,
            runtime_hours=latest.runtime_hours,
            baseline_vibration=machine.baseline_vibration,
            baseline_temperature=machine.baseline_temperature,
            baseline_current=machine.baseline_current,
            power_class=machine.power_class,
        )
        health_values.append(result.health_index)

        if result.health_index < settings.health_critical_threshold:
            critical += 1
        elif result.health_index < settings.health_warning_threshold:
            warning += 1
        else:
            healthy += 1

        worst.append(
            {
                "id": machine.id,
                "name": machine.name,
                "asset_tag": machine.asset_tag,
                "health_index": result.health_index,
                "vibration_zone": result.vibration_zone,
            }
        )

    worst.sort(key=lambda m: m["health_index"])

    open_alerts = db.scalar(select(func.count(Alert.id)).where(Alert.acknowledged.is_(False))) or 0
    open_orders = (
        db.scalar(select(func.count(WorkOrder.id)).where(WorkOrder.status.in_(_OPEN_STATUSES))) or 0
    )
    since = datetime.now(UTC) - timedelta(hours=24)
    actions_24h = (
        db.scalar(select(func.count(AutomationLog.id)).where(AutomationLog.created_at >= since))
        or 0
    )

    recent = db.scalars(
        select(AutomationLog).order_by(AutomationLog.created_at.desc()).limit(10)
    ).all()

    return DashboardSummary(
        machines_total=len(machines),
        machines_critical=critical,
        machines_warning=warning,
        machines_healthy=healthy,
        open_alerts=open_alerts,
        open_work_orders=open_orders,
        fleet_health_avg=(
            round(sum(health_values) / len(health_values), 1) if health_values else 0.0
        ),
        automation_actions_24h=actions_24h,
        worst_machines=worst[:5],
        recent_automation=[AutomationLogOut.model_validate(entry) for entry in recent],
    )
