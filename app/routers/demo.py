"""Demo seeding and telemetry simulation.

Two endpoints, both honest about what they are:

- POST /api/seed-demo creates a small fleet with 24 hours of history so a new
  account has something to look at. It is idempotent: calling it twice does not
  duplicate the fleet.
- POST /api/simulate appends synthetic readings to every machine the caller
  owns, so you can watch the engine react without wiring a real PLC.

The synthetic data is generated from a fixed seed per machine, so the same
machine always degrades the same way. That matters: a demo that changes every
refresh teaches you nothing about whether the engine works.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import engine
from ..db import get_db
from ..models import Machine, Reading, User
from ..security import current_user
from .machines import _sync_alert

router = APIRouter(prefix="/api", tags=["demo"])

# name, tag, line, type, nominal(v,t,a), critical(v,t,a), wear 0..1
DEMO_FLEET = [
    ("Cooling water pump", "PMP-101", "Line A", "pump", (2.0, 45.0, 12.0), (11.0, 95.0, 22.0), 0.12),
    ("Main air compressor", "CMP-204", "Line A", "compressor", (3.0, 55.0, 18.0), (12.0, 105.0, 30.0), 0.58),
    ("Conveyor drive motor", "MTR-310", "Line B", "motor", (1.8, 40.0, 9.0), (9.0, 90.0, 18.0), 0.31),
    ("Extraction fan", "FAN-402", "Line B", "fan", (2.4, 38.0, 7.0), (10.0, 85.0, 15.0), 0.86),
    ("Hydraulic power unit", "HPU-505", "Line C", "pump", (2.2, 50.0, 14.0), (10.5, 100.0, 26.0), 0.05),
]


def _sample(
    machine: Machine, wear: float, hours_ago: float, rng: random.Random
) -> tuple[float, float, float]:
    """One synthetic reading at a given wear level.

    Wear is a 0..1 fraction of the way from nominal to critical. We add a small
    amount of noise so the trend line has something to fit, and a slow sinusoid
    so the data is not a perfect ramp — real machines breathe.
    """
    breath = 1.0 + 0.04 * math.sin(hours_ago / 3.0)
    jitter = lambda scale: rng.gauss(0.0, scale)  # noqa: E731

    vib = machine.nominal_vibration + wear * (machine.critical_vibration - machine.nominal_vibration)
    temp = machine.nominal_temperature + wear * (machine.critical_temperature - machine.nominal_temperature)
    amp = machine.nominal_current + wear * (machine.critical_current - machine.nominal_current)

    return (
        max(0.0, vib * breath + jitter(0.12)),
        temp * breath + jitter(0.6),
        max(0.0, amp * breath + jitter(0.15)),
    )


def _seed_machine(db: Session, user: User, spec, hours: int = 24) -> Machine:
    name, tag, line, mtype, nominal, critical, wear = spec
    machine = Machine(
        owner_id=user.id,
        asset_tag=tag,
        name=name,
        line=line,
        machine_type=mtype,
        nominal_vibration=nominal[0],
        critical_vibration=critical[0],
        nominal_temperature=nominal[1],
        critical_temperature=critical[1],
        nominal_current=nominal[2],
        critical_current=critical[2],
    )
    db.add(machine)
    db.flush()

    rng = random.Random(f"{tag}-seed")
    now = datetime.now(UTC)
    # Wear climbs from (wear - 0.10) to wear across the window, so the machine
    # is visibly degrading rather than sitting flat.
    start_wear = max(0.0, wear - 0.10)

    for i in range(hours + 1):
        hours_ago = hours - i
        progress = i / hours
        level = start_wear + (wear - start_wear) * progress
        vib, temp, amp = _sample(machine, level, hours_ago, rng)
        ts = now - timedelta(hours=hours_ago)

        single = engine.evaluate(
            [(ts, vib, temp, amp)],
            (machine.nominal_vibration, machine.nominal_temperature, machine.nominal_current),
            (machine.critical_vibration, machine.critical_temperature, machine.critical_current),
        )
        db.add(
            Reading(
                machine_id=machine.id,
                recorded_at=ts,
                vibration_rms=round(vib, 3),
                temperature_c=round(temp, 2),
                current_a=round(amp, 3),
                health=single.health,
            )
        )

    return machine


def _sync_machine_alert(db: Session, machine: Machine) -> None:
    """Evaluate a machine's recent window and raise/clear its alert.

    Seeding writes readings directly, which bypasses the ingestion endpoint —
    so without this the demo fleet shows a red machine with an empty alert
    inbox. That is exactly the kind of inconsistency that makes a dashboard
    untrustworthy, so seeding runs the same verdict path as live ingestion.
    """
    rows = list(
        db.scalars(
            select(Reading)
            .where(Reading.machine_id == machine.id)
            .order_by(Reading.recorded_at.desc())
            .limit(engine.TREND_WINDOW)
        )
    )
    if not rows:
        return
    rows.reverse()
    report = engine.evaluate(
        [(r.recorded_at, r.vibration_rms, r.temperature_c, r.current_a) for r in rows],
        (machine.nominal_vibration, machine.nominal_temperature, machine.nominal_current),
        (machine.critical_vibration, machine.critical_temperature, machine.critical_current),
    )
    _sync_alert(machine, report, db)


@router.post("/seed-demo", status_code=status.HTTP_201_CREATED)
def seed_demo(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Create the demo fleet for this user, once."""
    existing = db.scalar(select(Machine).where(Machine.owner_id == user.id).limit(1))
    if existing is not None:
        return {"created": 0, "note": "Fleet already exists — nothing seeded."}

    machines = [_seed_machine(db, user, spec) for spec in DEMO_FLEET]
    db.flush()
    for machine in machines:
        _sync_machine_alert(db, machine)

    db.commit()
    return {"created": len(DEMO_FLEET), "note": "Demo fleet seeded with 24 hours of history."}


@router.post("/simulate", status_code=status.HTTP_201_CREATED)
def simulate(
    payload: dict | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Append synthetic readings to every machine the caller owns.

    Each machine continues from its own last reading, drifting a little further
    toward critical. This is a demo aid, not a physics model — the response says
    so, and the UI labels the button "Simulate 24h".
    """
    hours = int((payload or {}).get("hours", 24))
    hours = max(1, min(hours, 168))

    machines = list(db.scalars(select(Machine).where(Machine.owner_id == user.id)))
    if not machines:
        return {"machines": 0, "readings_created": 0, "note": "No machines to simulate."}

    created = 0
    now = datetime.now(UTC)

    for machine in machines:
        last = db.scalar(
            select(Reading)
            .where(Reading.machine_id == machine.id)
            .order_by(Reading.recorded_at.desc())
            .limit(1)
        )
        if last is None:
            continue

        # Where is this machine on the nominal→critical scale right now?
        span = machine.critical_vibration - machine.nominal_vibration
        current_wear = (last.vibration_rms - machine.nominal_vibration) / span if span > 0 else 0.0
        current_wear = max(0.0, min(1.0, current_wear))

        rng = random.Random(f"{machine.asset_tag}-{int(now.timestamp())}")
        # Drift 0.4% of the scale per simulated hour — slow enough to be believable.
        drift_per_hour = 0.004

        for i in range(1, hours + 1):
            level = min(1.0, current_wear + drift_per_hour * i)
            vib, temp, amp = _sample(machine, level, hours - i, rng)
            ts = now - timedelta(hours=hours - i)

            single = engine.evaluate(
                [(ts, vib, temp, amp)],
                (machine.nominal_vibration, machine.nominal_temperature, machine.nominal_current),
                (machine.critical_vibration, machine.critical_temperature, machine.critical_current),
            )
            db.add(
                Reading(
                    machine_id=machine.id,
                    recorded_at=ts,
                    vibration_rms=round(vib, 3),
                    temperature_c=round(temp, 2),
                    current_a=round(amp, 3),
                    health=single.health,
                )
            )
            created += 1

    db.flush()
    for machine in machines:
        _sync_machine_alert(db, machine)

    db.commit()
    return {
        "machines": len(machines),
        "readings_created": created,
        "note": "Synthetic telemetry. Not a physics model — for demo only.",
    }
