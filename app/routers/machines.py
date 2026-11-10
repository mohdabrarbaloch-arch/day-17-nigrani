"""Plants and machines."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.engine.health import estimate_rul, score_health
from app.models import Alert, Machine, Plant, Reading, User, WorkOrder, WorkOrderStatus
from app.schemas import MachineCreate, MachineDetail, MachineOut, PlantCreate, PlantOut
from app.timeutil import days_between

router = APIRouter(prefix="/api", tags=["machines"])


@router.post("/plants", response_model=PlantOut, status_code=status.HTTP_201_CREATED)
def create_plant(
    payload: PlantCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> PlantOut:
    plant = Plant(name=payload.name, city=payload.city)
    db.add(plant)
    db.commit()
    db.refresh(plant)
    return PlantOut.model_validate(plant)


@router.get("/plants", response_model=list[PlantOut])
def list_plants(
    db: Session = Depends(get_db), _user: User = Depends(get_current_user)
) -> list[PlantOut]:
    plants = db.scalars(select(Plant).order_by(Plant.name)).all()
    return [PlantOut.model_validate(p) for p in plants]


@router.post("/machines", response_model=MachineOut, status_code=status.HTTP_201_CREATED)
def create_machine(
    payload: MachineCreate,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> MachineOut:
    if db.get(Plant, payload.plant_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plant not found")

    if db.scalar(select(Machine).where(Machine.asset_tag == payload.asset_tag)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Asset tag already registered"
        )

    machine = Machine(**payload.model_dump())
    db.add(machine)
    db.commit()
    db.refresh(machine)
    return MachineOut.model_validate(machine)


@router.get("/machines", response_model=list[MachineOut])
def list_machines(
    plant_id: int | None = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[MachineOut]:
    stmt = select(Machine).order_by(Machine.name)
    if plant_id is not None:
        stmt = stmt.where(Machine.plant_id == plant_id)
    machines = db.scalars(stmt).all()
    return [MachineOut.model_validate(m) for m in machines]


@router.get("/machines/{machine_id}", response_model=MachineDetail)
def get_machine(
    machine_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> MachineDetail:
    machine = db.get(Machine, machine_id)
    if machine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Machine not found")

    detail = MachineDetail.model_validate(machine)

    latest = db.scalar(
        select(Reading)
        .where(Reading.machine_id == machine_id)
        .order_by(Reading.recorded_at.desc())
        .limit(1)
    )
    if latest is not None:
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
        detail.health_index = result.health_index
        detail.vibration_zone = result.vibration_zone

        history = db.scalars(
            select(Reading)
            .where(Reading.machine_id == machine_id)
            .order_by(Reading.recorded_at.desc())
            .limit(20)
        ).all()
        if len(history) >= 3:
            newest = history[0].recorded_at
            series = [(days_between(newest, r.recorded_at), r.health_index) for r in history]
            rul = estimate_rul(series)
            detail.rul_days = rul.days_remaining
            detail.rul_confidence = rul.confidence

    detail.open_alerts = (
        db.scalar(
            select(func.count(Alert.id)).where(
                Alert.machine_id == machine_id, Alert.acknowledged.is_(False)
            )
        )
        or 0
    )
    detail.open_work_orders = (
        db.scalar(
            select(func.count(WorkOrder.id)).where(
                WorkOrder.machine_id == machine_id,
                WorkOrder.status.in_(
                    [WorkOrderStatus.OPEN, WorkOrderStatus.ASSIGNED, WorkOrderStatus.IN_PROGRESS]
                ),
            )
        )
        or 0
    )
    return detail
