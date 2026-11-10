"""Seed a demo plant with machines and a realistic telemetry history.

Run:  python -m app.seed
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.engine.alerts import decide_alert
from app.engine.health import score_health
from app.models import Alert, Machine, Plant, Reading, User
from app.rpa.runbooks import open_work_order_for_alert
from app.security import hash_password

MACHINES = [
    # name, tag, type, power_class, base_vib, base_temp, base_current, hours, drift
    ("Cooling water pump 2", "KHI-PMP-014", "pump", 2, 2.8, 62.0, 10.0, 8_400, 0.55),
    ("Boiler feed pump A", "KHI-PMP-021", "pump", 3, 3.5, 68.0, 22.0, 14_200, 0.30),
    ("Air compressor 1", "KHI-CMP-003", "compressor", 2, 2.6, 70.0, 18.0, 6_100, 0.10),
    ("Conveyor drive motor", "KHI-MTR-007", "motor", 1, 1.8, 55.0, 6.5, 3_200, 0.05),
    ("Chiller unit B", "KHI-CHL-002", "chiller", 3, 3.2, 58.0, 30.0, 11_800, 0.42),
    ("Hydraulic power pack", "KHI-HYD-011", "pump", 2, 2.9, 66.0, 12.0, 9_600, 0.20),
]


def seed() -> None:
    init_db()
    rng = random.Random(17)  # deterministic demo data

    with SessionLocal() as db:
        if db.scalar(select(Plant).limit(1)) is not None:
            print("Database already has data — skipping seed.")
            return

        if db.scalar(select(User).limit(1)) is None:
            db.add(
                User(
                    email="admin@nigrani.dev",
                    full_name="Plant Administrator",
                    hashed_password=hash_password("ChangeMe123!"),
                    role="admin",
                )
            )

        plant = Plant(name="Karachi Plant 1", city="Karachi")
        db.add(plant)
        db.flush()

        now = datetime.now(UTC)
        total_readings = 0

        for (
            name,
            tag,
            mtype,
            pclass,
            base_vib,
            base_temp,
            base_cur,
            hours,
            drift,
        ) in MACHINES:
            machine = Machine(
                plant_id=plant.id,
                name=name,
                asset_tag=tag,
                machine_type=mtype,
                power_class=pclass,
                baseline_vibration=base_vib,
                baseline_temperature=base_temp,
                baseline_current=base_cur,
                runtime_hours=hours,
            )
            db.add(machine)
            db.flush()

            # 14 days of history, one reading per day, degrading by `drift` per day.
            last_result = None
            for day in range(13, -1, -1):
                vib = base_vib + drift * (13 - day) + rng.uniform(-0.15, 0.15)
                temp = base_temp + drift * 4.0 * (13 - day) + rng.uniform(-0.8, 0.8)
                cur = base_cur * (1 + 0.012 * (13 - day)) + rng.uniform(-0.2, 0.2)

                result = score_health(
                    vibration_rms=max(0.1, vib),
                    temperature_c=temp,
                    current_a=max(0.1, cur),
                    runtime_hours=hours + (13 - day) * 24,
                    baseline_vibration=base_vib,
                    baseline_temperature=base_temp,
                    baseline_current=base_cur,
                    power_class=pclass,
                )

                db.add(
                    Reading(
                        machine_id=machine.id,
                        vibration_rms=round(max(0.1, vib), 2),
                        temperature_c=round(temp, 1),
                        current_a=round(max(0.1, cur), 2),
                        runtime_hours=hours + (13 - day) * 24,
                        health_index=result.health_index,
                        recorded_at=now - timedelta(days=day),
                    )
                )
                total_readings += 1
                last_result = result

            machine.runtime_hours = hours + 13 * 24

            # Make the demo coherent: a machine that ends up critical should
            # already have the alert and work order the RPA would have opened.
            if last_result is not None:
                decision = decide_alert(machine.name, last_result)
                if decision.should_alert:
                    alert = Alert(
                        machine_id=machine.id,
                        severity=decision.severity,
                        title=decision.title,
                        detail=decision.detail,
                        health_index=last_result.health_index,
                    )
                    db.add(alert)
                    db.flush()
                    open_work_order_for_alert(db, machine=machine, alert=alert, decision=decision)

        db.commit()
        print(
            f"Seeded plant '{plant.name}' with {len(MACHINES)} machines, "
            f"{total_readings} readings."
        )
        print("Sign in with admin@nigrani.dev / ChangeMe123!")


if __name__ == "__main__":
    seed()
