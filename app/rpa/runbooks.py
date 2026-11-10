"""RPA runbooks — the part that actually *does* something.

Each runbook is idempotent: it checks for an existing open work order before
creating one, so a retried webhook cannot open duplicates. Every step writes an
AutomationLog row, which is what makes the automation auditable.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.engine.alerts import AlertDecision, priority_for_severity
from app.models import (
    Alert,
    AutomationLog,
    Machine,
    WorkOrder,
    WorkOrderStatus,
)

TECHNICIANS = [
    "Imran Qureshi",
    "Sana Memon",
    "Bilal Ahmed",
    "Hina Raza",
    "Usman Tariq",
]


def _log(
    db: Session,
    *,
    runbook: str,
    action: str,
    outcome: str,
    detail: str = "",
    machine_id: int | None = None,
) -> AutomationLog:
    entry = AutomationLog(
        runbook=runbook,
        machine_id=machine_id,
        action=action,
        outcome=outcome,
        detail=detail,
    )
    db.add(entry)
    return entry


def pick_technician(db: Session) -> str:
    """Least-loaded technician — fewest open work orders, ties broken by name."""
    counts = dict(
        db.execute(
            select(WorkOrder.assigned_to, func.count(WorkOrder.id))
            .where(
                WorkOrder.status.in_(
                    [WorkOrderStatus.OPEN, WorkOrderStatus.ASSIGNED, WorkOrderStatus.IN_PROGRESS]
                ),
                WorkOrder.assigned_to.is_not(None),
            )
            .group_by(WorkOrder.assigned_to)
        ).all()
    )
    return min(TECHNICIANS, key=lambda name: (counts.get(name, 0), name))


def open_work_order_for_alert(
    db: Session,
    *,
    machine: Machine,
    alert: Alert,
    decision: AlertDecision,
) -> WorkOrder | None:
    """Create a work order for an alert, unless one is already open for it."""
    existing = db.scalar(
        select(WorkOrder).where(
            WorkOrder.alert_id == alert.id,
            WorkOrder.status.in_(
                [WorkOrderStatus.OPEN, WorkOrderStatus.ASSIGNED, WorkOrderStatus.IN_PROGRESS]
            ),
        )
    )
    if existing is not None:
        _log(
            db,
            runbook="open_work_order",
            action="create_work_order",
            outcome="skipped",
            detail=f"Work order #{existing.id} already open for alert #{alert.id}",
            machine_id=machine.id,
        )
        return existing

    technician = pick_technician(db)
    order = WorkOrder(
        machine_id=machine.id,
        alert_id=alert.id,
        title=f"[{decision.severity.upper()}] {machine.name} — {machine.asset_tag}",
        description=(
            f"{decision.detail}\n\n"
            f"Machine: {machine.name} ({machine.asset_tag})\n"
            f"Type: {machine.machine_type} · Power class {machine.power_class}\n"
            f"Health index at alert: {alert.health_index:.0f}/100\n\n"
            "Suggested first checks: bearing temperature, lubrication, alignment, "
            "and a full vibration spectrum against the commissioning baseline."
        ),
        priority=priority_for_severity(decision.severity),
        status=WorkOrderStatus.ASSIGNED,
        assigned_to=technician,
        created_by="rpa",
    )
    db.add(order)
    db.flush()

    _log(
        db,
        runbook="open_work_order",
        action="create_work_order",
        outcome="created",
        detail=(
            f"Work order #{order.id} ({order.priority}) assigned to {technician} "
            f"for alert #{alert.id}"
        ),
        machine_id=machine.id,
    )
    return order


def escalate_stale_orders(db: Session, *, hours: int = 24) -> int:
    """Bump priority on work orders that have sat open past the escalation window."""
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    stale = db.scalars(
        select(WorkOrder).where(
            WorkOrder.status == WorkOrderStatus.OPEN,
            WorkOrder.created_at < cutoff,
        )
    ).all()

    escalated = 0
    for order in stale:
        if order.priority != "urgent":
            order.priority = "urgent"
            escalated += 1
            _log(
                db,
                runbook="escalate_stale",
                action="escalate_priority",
                outcome="escalated",
                detail=f"Work order #{order.id} unassigned for over {hours}h — set to urgent",
                machine_id=order.machine_id,
            )
    return escalated
