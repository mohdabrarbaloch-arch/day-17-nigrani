"""Request and response models.

Pydantic v2. Every field that reaches the engine is bounded here, so the
engine never has to defend itself against a negative vibration reading.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="Engineer", min_length=1, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    display_name: str
    role: str


class MachineCreate(BaseModel):
    asset_tag: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=160)
    line: str = Field(default="Line A", max_length=80)
    machine_type: str = Field(default="pump", max_length=60)
    nominal_vibration: float = Field(default=2.0, ge=0, le=100)
    critical_vibration: float = Field(default=11.0, gt=0, le=200)
    nominal_temperature: float = Field(default=45.0, ge=-50, le=300)
    critical_temperature: float = Field(default=95.0, gt=-50, le=500)
    nominal_current: float = Field(default=12.0, ge=0, le=1000)
    critical_current: float = Field(default=22.0, gt=0, le=2000)


class MachineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    asset_tag: str
    name: str
    line: str
    machine_type: str
    nominal_vibration: float
    critical_vibration: float
    nominal_temperature: float
    critical_temperature: float
    nominal_current: float
    critical_current: float
    is_active: bool
    created_at: datetime


class ReadingIn(BaseModel):
    vibration_rms: float = Field(ge=0, le=200)
    temperature_c: float = Field(ge=-50, le=500)
    current_a: float = Field(ge=0, le=2000)
    recorded_at: datetime | None = None


class ChannelOut(BaseModel):
    name: str
    value: float
    nominal: float
    critical: float
    score: float
    weight: float


class HealthOut(BaseModel):
    machine_id: int
    asset_tag: str
    name: str
    line: str
    health: float
    worst_channel: str
    degradation_per_hour: float | None
    rul_hours: float | None
    severity: str | None
    reason: str | None
    channels: list[ChannelOut]
    reading_count: int


class ReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    recorded_at: datetime
    vibration_rms: float
    temperature_c: float
    current_a: float
    health: float


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    machine_id: int
    severity: str
    reason: str
    health_at_alert: float
    rul_hours: float | None
    raised_at: datetime
    resolved_at: datetime | None
    resolved_note: str | None


class ResolveAlertRequest(BaseModel):
    note: str = Field(min_length=1, max_length=500)


class MaintenanceCreate(BaseModel):
    action: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    downtime_minutes: int = Field(default=0, ge=0, le=100000)


class MaintenanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    machine_id: int
    performed_at: datetime
    action: str
    notes: str | None
    downtime_minutes: int
    performed_by: str


class FleetSummary(BaseModel):
    total: int
    healthy: int
    warning: int
    critical: int
    emergency: int
    open_alerts: int
    machines: list[HealthOut]
