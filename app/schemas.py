"""Pydantic request/response contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# ── Auth ──────────────────────────────────────────────────────────────────────
class UserRegister(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    role: str
    is_active: bool
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ── Plants ────────────────────────────────────────────────────────────────────
class PlantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    city: str = Field(min_length=2, max_length=80)


class PlantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    city: str
    created_at: datetime


# ── Machines ──────────────────────────────────────────────────────────────────
class MachineCreate(BaseModel):
    plant_id: int
    name: str = Field(min_length=2, max_length=120)
    asset_tag: str = Field(min_length=2, max_length=64)
    machine_type: str = Field(default="pump", max_length=64)
    power_class: int = Field(default=2, ge=1, le=3)
    baseline_vibration: float = Field(default=2.8, ge=0, le=100)
    baseline_temperature: float = Field(default=62.0, ge=-40, le=300)
    baseline_current: float = Field(default=10.0, gt=0, le=10_000)
    runtime_hours: float = Field(default=0.0, ge=0)


class MachineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plant_id: int
    name: str
    asset_tag: str
    machine_type: str
    power_class: int
    status: str
    baseline_vibration: float
    baseline_temperature: float
    baseline_current: float
    runtime_hours: float
    created_at: datetime


class MachineDetail(MachineOut):
    health_index: float | None = None
    vibration_zone: str | None = None
    rul_days: float | None = None
    rul_confidence: str | None = None
    open_alerts: int = 0
    open_work_orders: int = 0


# ── Telemetry ─────────────────────────────────────────────────────────────────
class ReadingCreate(BaseModel):
    vibration_rms: float = Field(ge=0, le=500)
    temperature_c: float = Field(ge=-60, le=500)
    current_a: float = Field(ge=0, le=10_000)
    runtime_hours: float = Field(ge=0, le=1_000_000)
    recorded_at: datetime | None = None

    @field_validator("vibration_rms", "temperature_c", "current_a", "runtime_hours")
    @classmethod
    def must_be_finite(cls, v: float) -> float:
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("value must be a finite number")
        return v


class ReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    machine_id: int
    vibration_rms: float
    temperature_c: float
    current_a: float
    runtime_hours: float
    health_index: float
    recorded_at: datetime


class BreakdownOut(BaseModel):
    vibration: float
    thermal: float
    current: float
    duty: float
    notes: list[str]


class IngestResult(BaseModel):
    reading: ReadingOut
    health_index: float
    vibration_zone: str
    breakdown: BreakdownOut
    alert_created: bool
    work_order_id: int | None = None
    rul_days: float | None = None
    rul_confidence: str | None = None


# ── Operations ────────────────────────────────────────────────────────────────
class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    machine_id: int
    severity: str
    title: str
    detail: str
    health_index: float
    acknowledged: bool
    created_at: datetime


class WorkOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    machine_id: int
    alert_id: int | None
    title: str
    description: str
    priority: str
    status: str
    assigned_to: str | None
    created_by: str
    created_at: datetime
    closed_at: datetime | None


class WorkOrderUpdate(BaseModel):
    status: str | None = None
    assigned_to: str | None = Field(default=None, max_length=120)


class AutomationLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    runbook: str
    machine_id: int | None
    action: str
    outcome: str
    detail: str
    created_at: datetime


# ── Dashboard ─────────────────────────────────────────────────────────────────
class WorstMachine(BaseModel):
    id: int
    name: str
    asset_tag: str
    health_index: float
    vibration_zone: str


class DashboardSummary(BaseModel):
    machines_total: int
    machines_critical: int
    machines_warning: int
    machines_healthy: int
    open_alerts: int
    open_work_orders: int
    fleet_health_avg: float
    automation_actions_24h: int
    worst_machines: list[WorstMachine]
    recent_automation: list[AutomationLogOut]
