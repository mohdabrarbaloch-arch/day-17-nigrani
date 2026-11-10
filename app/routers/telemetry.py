"""Telemetry ingestion — the heart of the system.

A reading comes in, gets scored, and if the score crosses a threshold the RPA
runbook fires. All of that happens in one transaction so a partial failure
cannot leave an alert without its work order.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.engine.alerts import decide_alert
from app.engine.health import estimate_rul, score_health
from app.models import Alert, Machine, Reading, User
from app.rpa.runbooks import open_work_order_for_alert
from app.schemas import IngestResult, ReadingCreate, ReadingOut
from app.timeutil import days_between

router = APIRouter(prefix="/api", tags=["telemetry"])


@router.post(
    "/machines/{machine_id}/readings",
    response_model=IngestResult,
    status_code=status.HTTP_201_CREATED,
)
def ingest_reading(
    machine_id: int,
    payload: ReadingCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> IngestResult:
    machine = db.get(Machine, machine_id)
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")

    result = score_health(
        vibration_rms=payload.vibration_rms,
        temperature_c=payload.temperature_c,
        current_a=payload.current_a,
        runtime_hours=payload.runtime_hours,
        baseline_vibration=machine.baseline_vibration,
        baseline_temperature=machine.baseline_temperature,
        baseline_current=machine.baseline_current,
        power_class=machine.power_class,
    )

    reading = Reading(
        machine_id=machine_id,
        vibration_rms=payload.vibration_rms,
        temperature_c=payload.temperature_c,
        current_a=payload.current_a,
        runtime_hours=payload.runtime_hours,
        health_index=result.health_index,
        recorded_at=payload.recorded_at or datetime.now(UTC),
    )
    db.add(reading)
    machine.runtime_hours = max(machine.runtime_hours, payload.runtime_hours)
    db.flush()

    # ── RPA: decide, then act ────────────────────────────────────────────────
    decision = decide_alert(machine.name, result)
    alert_created = False
    work_order_id: int | None = None

    if decision.should_alert:
        alert = Alert(
            machine_id=machine_id,
            severity=decision.severity,
            title=decision.title,
            detail=decision.detail,
            health_index=result.health_index,
        )
        db.add(alert)
        db.flush()
        alert_created = True

        if settings.rpa_auto_work_orders:
            order = open_work_order_for_alert(db, machine=machine, alert=alert, decision=decision)
            work_order_id = order.id if order is not None else None

    # ── RUL over recent history ──────────────────────────────────────────────
    history = db.scalars(
        select(Reading)
        .where(Reading.machine_id == machine_id)
        .order_by(Reading.recorded_at.desc())
        .limit(settings.rul_history_window)
    ).all()

    rul_days: float | None = None
    rul_confidence: str | None = None
    if len(history) >= 3:
        newest = history[0].recorded_at
        series = [(days_between(newest, r.recorded_at), r.health_index) for r in history]
        rul = estimate_rul(series, window=settings.rul_history_window)
        rul_days = rul.days_remaining
        rul_confidence = rul.confidence

    db.commit()
    db.refresh(reading)

    return IngestResult(
        reading=ReadingOut.model_validate(reading),
        health_index=result.health_index,
        vibration_zone=result.vibration_zone,
        breakdown={
            "vibration": result.breakdown.vibration,
            "thermal": result.breakdown.thermal,
            "current": result.breakdown.current,
            "duty": result.breakdown.duty,
            "notes": result.breakdown.notes,
        },
        alert_created=alert_created,
        work_order_id=work_order_id,
        rul_days=rul_days,
        rul_confidence=rul_confidence,
    )


@router.get("/machines/{machine_id}/readings", response_model=list[ReadingOut])
def list_readings(
    machine_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[ReadingOut]:
    if db.get(Machine, machine_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")
    limit = max(1, min(limit, 500))
    readings = db.scalars(
        select(Reading)
        .where(Reading.machine_id == machine_id)
        .order_by(Reading.recorded_at.desc())
        .limit(limit)
    ).all()
    return [ReadingOut.model_validate(r) for r in readings]
