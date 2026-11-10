"""Machine CRUD, telemetry ingestion, health evaluation and maintenance logs.

The interesting endpoint is POST /{id}/readings: it stores the sample, runs the
engine, and raises or clears an alert in the same transaction. That way the
dashboard never shows a reading without the verdict that belongs to it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import engine
from ..db import get_db
from ..models import Alert, Machine, MaintenanceLog, Reading, User
from ..schemas import (
    ChannelOut,
    HealthOut,
    MachineCreate,
    MachineOut,
    MaintenanceCreate,
    MaintenanceOut,
    ReadingIn,
    ReadingOut,
)
from ..security import current_user

router = APIRouter(prefix="/api/machines", tags=["machines"])


def _owned(machine_id: int, user: User, db: Session) -> Machine:
    machine = db.get(Machine, machine_id)
    if machine is None or machine.owner_id != user.id:
        # 404 rather than 403: do not confirm that someone else's machine exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Machine not found")
    return machine


def _nominal(m: Machine) -> tuple[float, float, float]:
    return (m.nominal_vibration, m.nominal_temperature, m.nominal_current)


def _critical(m: Machine) -> tuple[float, float, float]:
    return (m.critical_vibration, m.critical_temperature, m.critical_current)


def _report_for(machine: Machine, readings: list[Reading]) -> engine.HealthReport | None:
    if not readings:
        return None
    rows = [(r.recorded_at, r.vibration_rms, r.temperature_c, r.current_a) for r in readings]
    return engine.evaluate(rows, _nominal(machine), _critical(machine))


def _health_out(machine: Machine, readings: list[Reading]) -> HealthOut:
    report = _report_for(machine, readings)
    if report is None:
        return HealthOut(
            machine_id=machine.id,
            asset_tag=machine.asset_tag,
            name=machine.name,
            line=machine.line,
            health=100.0,
            worst_channel="none",
            degradation_per_hour=None,
            rul_hours=None,
            severity=None,
            reason="No telemetry yet — health is unknown, shown as 100 until the first reading.",
            channels=[],
            reading_count=0,
        )
    return HealthOut(
        machine_id=machine.id,
        asset_tag=machine.asset_tag,
        name=machine.name,
        line=machine.line,
        health=report.health,
        worst_channel=report.worst_channel,
        degradation_per_hour=report.degradation_per_hour,
        rul_hours=report.rul_hours,
        severity=report.severity,
        reason=report.reason,
        channels=[
            ChannelOut(
                name=c.name,
                value=c.value,
                nominal=c.nominal,
                critical=c.critical,
                score=round(c.score, 4),
                weight=c.weight,
            )
            for c in report.channels
        ],
        reading_count=len(readings),
    )


@router.post("", response_model=MachineOut, status_code=status.HTTP_201_CREATED)
def create_machine(
    payload: MachineCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Machine:
    clash = db.scalar(
        select(Machine).where(Machine.owner_id == user.id, Machine.asset_tag == payload.asset_tag)
    )
    if clash is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Asset tag {payload.asset_tag!r} is already in use")

    for nominal, critical, label in (
        (payload.nominal_vibration, payload.critical_vibration, "vibration"),
        (payload.nominal_temperature, payload.critical_temperature, "temperature"),
        (payload.nominal_current, payload.critical_current, "current"),
    ):
        if critical <= nominal:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"critical_{label} must exceed nominal_{label}",
            )

    machine = Machine(owner_id=user.id, **payload.model_dump())
    db.add(machine)
    db.commit()
    db.refresh(machine)
    return machine


@router.get("", response_model=list[MachineOut])
def list_machines(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[Machine]:
    return list(db.scalars(select(Machine).where(Machine.owner_id == user.id).order_by(Machine.asset_tag)))


@router.get("/{machine_id}", response_model=MachineOut)
def get_machine(
    machine_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Machine:
    return _owned(machine_id, user, db)


@router.delete("/{machine_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_machine(
    machine_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    machine = _owned(machine_id, user, db)
    db.delete(machine)
    db.commit()


@router.post("/{machine_id}/readings", response_model=HealthOut, status_code=status.HTTP_201_CREATED)
def add_reading(
    machine_id: int,
    payload: ReadingIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> HealthOut:
    machine = _owned(machine_id, user, db)

    recorded_at = payload.recorded_at or datetime.now(UTC)
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=UTC)

    # Score this sample on its own first so the stored row carries its health.
    single = engine.evaluate(
        [(recorded_at, payload.vibration_rms, payload.temperature_c, payload.current_a)],
        _nominal(machine),
        _critical(machine),
    )

    reading = Reading(
        machine_id=machine.id,
        recorded_at=recorded_at,
        vibration_rms=payload.vibration_rms,
        temperature_c=payload.temperature_c,
        current_a=payload.current_a,
        health=single.health,
    )
    db.add(reading)
    db.flush()

    history = list(
        db.scalars(
            select(Reading)
            .where(Reading.machine_id == machine.id)
            .order_by(Reading.recorded_at.desc())
            .limit(engine.TREND_WINDOW)
        )
    )
    history.reverse()

    report = _report_for(machine, history)
    assert report is not None  # we just inserted one

    _sync_alert(machine, report, db)

    db.commit()
    return _health_out(machine, history)


def _sync_alert(machine: Machine, report: engine.HealthReport, db: Session) -> None:
    """Raise an alert if needed, and clear stale ones.

    One open alert per severity. If the machine recovers above the warning
    band, every open alert is auto-resolved with a note saying so — a stale
    red banner is worse than no banner.
    """
    open_alerts = list(
        db.scalars(select(Alert).where(Alert.machine_id == machine.id, Alert.resolved_at.is_(None)))
    )

    if report.severity is None:
        for alert in open_alerts:
            alert.resolved_at = datetime.now(UTC)
            alert.resolved_note = f"Auto-resolved: health recovered to {report.health:.1f}."
        return

    same = [a for a in open_alerts if a.severity == report.severity]
    if same:
        # Refresh the numbers on the existing alert instead of stacking a new one.
        same[0].health_at_alert = report.health
        same[0].rul_hours = report.rul_hours
        same[0].reason = report.reason or same[0].reason
        return

    # Severity changed — close the older, lower-severity ones.
    for alert in open_alerts:
        alert.resolved_at = datetime.now(UTC)
        alert.resolved_note = f"Superseded by a {report.severity} alert."

    db.add(
        Alert(
            machine_id=machine.id,
            severity=report.severity,
            reason=report.reason or "Health below threshold",
            health_at_alert=report.health,
            rul_hours=report.rul_hours,
        )
    )


@router.get("/{machine_id}/health", response_model=HealthOut)
def machine_health(
    machine_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> HealthOut:
    machine = _owned(machine_id, user, db)
    readings = list(
        db.scalars(
            select(Reading)
            .where(Reading.machine_id == machine.id)
            .order_by(Reading.recorded_at.desc())
            .limit(engine.TREND_WINDOW)
        )
    )
    readings.reverse()
    return _health_out(machine, readings)


@router.get("/{machine_id}/readings", response_model=list[ReadingOut])
def list_readings(
    machine_id: int,
    limit: int = Query(default=100, ge=1, le=1000),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[Reading]:
    machine = _owned(machine_id, user, db)
    rows = list(
        db.scalars(
            select(Reading)
            .where(Reading.machine_id == machine.id)
            .order_by(Reading.recorded_at.desc())
            .limit(limit)
        )
    )
    rows.reverse()
    return rows


@router.get("/{machine_id}/forecast")
def forecast(
    machine_id: int,
    hours: int = Query(default=72, ge=1, le=720),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Projected health curve at the current degradation rate.

    This is a straight-line projection, not a model. It is labelled as such in
    the UI because a curve that looks predictive but is not is worse than no
    curve at all.
    """
    machine = _owned(machine_id, user, db)
    readings = list(
        db.scalars(
            select(Reading)
            .where(Reading.machine_id == machine.id)
            .order_by(Reading.recorded_at.desc())
            .limit(engine.TREND_WINDOW)
        )
    )
    readings.reverse()
    report = _report_for(machine, readings)
    if report is None:
        return {"machine_id": machine.id, "points": [], "note": "No telemetry yet."}

    step = max(1, hours // 24)
    points = [
        {
            "hours_ahead": h,
            "health": engine.project_health(report.health, report.degradation_per_hour, h),
        }
        for h in range(0, hours + 1, step)
    ]
    return {
        "machine_id": machine.id,
        "asset_tag": machine.asset_tag,
        "current_health": report.health,
        "degradation_per_hour": report.degradation_per_hour,
        "rul_hours": report.rul_hours,
        "points": points,
        "note": "Straight-line projection at the current rate. Not a trained forecast.",
    }


@router.post("/{machine_id}/maintenance", response_model=MaintenanceOut, status_code=status.HTTP_201_CREATED)
def log_maintenance(
    machine_id: int,
    payload: MaintenanceCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MaintenanceLog:
    machine = _owned(machine_id, user, db)
    log = MaintenanceLog(
        machine_id=machine.id,
        action=payload.action,
        notes=payload.notes,
        downtime_minutes=payload.downtime_minutes,
        performed_by=user.display_name,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


@router.get("/{machine_id}/maintenance", response_model=list[MaintenanceOut])
def list_maintenance(
    machine_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[MaintenanceLog]:
    machine = _owned(machine_id, user, db)
    return list(
        db.scalars(
            select(MaintenanceLog)
            .where(MaintenanceLog.machine_id == machine.id)
            .order_by(MaintenanceLog.performed_at.desc())
        )
    )
