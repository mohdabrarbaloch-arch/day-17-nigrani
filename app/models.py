"""Database models for Nigrani.

SQLAlchemy 2.0 typed style. SQLite by default, PostgreSQL when DATABASE_URL
points at one — the same code runs on both.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="Engineer")
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="engineer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    machines: Mapped[list[Machine]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class Machine(Base):
    """A physical asset being monitored.

    `nominal_*` is what the machine reads when it is healthy. `critical_*` is
    where it is considered failed. Both are per-machine because a 3-phase
    compressor and a small pump do not share a scale.
    """

    __tablename__ = "machines"
    __table_args__ = (UniqueConstraint("owner_id", "asset_tag", name="uq_machine_owner_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    asset_tag: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    line: Mapped[str] = mapped_column(String(80), nullable=False, default="Line A")
    machine_type: Mapped[str] = mapped_column(String(60), nullable=False, default="pump")

    nominal_vibration: Mapped[float] = mapped_column(Float, default=2.0)
    critical_vibration: Mapped[float] = mapped_column(Float, default=11.0)
    nominal_temperature: Mapped[float] = mapped_column(Float, default=45.0)
    critical_temperature: Mapped[float] = mapped_column(Float, default=95.0)
    nominal_current: Mapped[float] = mapped_column(Float, default=12.0)
    critical_current: Mapped[float] = mapped_column(Float, default=22.0)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    owner: Mapped[User] = relationship(back_populates="machines")
    readings: Mapped[list[Reading]] = relationship(
        back_populates="machine", cascade="all, delete-orphan", order_by="Reading.recorded_at"
    )
    alerts: Mapped[list[Alert]] = relationship(back_populates="machine", cascade="all, delete-orphan")


class Reading(Base):
    """One telemetry sample. Three channels, one timestamp."""

    __tablename__ = "readings"
    __table_args__ = (Index("ix_readings_machine_time", "machine_id", "recorded_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    vibration_rms: Mapped[float] = mapped_column(Float, nullable=False)
    temperature_c: Mapped[float] = mapped_column(Float, nullable=False)
    current_a: Mapped[float] = mapped_column(Float, nullable=False)
    health: Mapped[float] = mapped_column(Float, nullable=False)

    machine: Mapped[Machine] = relationship(back_populates="readings")


class Alert(Base):
    """An alert raised by the engine.

    One open alert per (machine, severity) at a time, so a degrading bearing
    does not page someone every minute.
    """

    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_machine_open", "machine_id", "resolved_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    health_at_alert: Mapped[float] = mapped_column(Float, nullable=False)
    rul_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    machine: Mapped[Machine] = relationship(back_populates="alerts")


class MaintenanceLog(Base):
    """What a human actually did, and when. Closes the loop on an alert."""

    __tablename__ = "maintenance_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id", ondelete="CASCADE"), index=True)
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    downtime_minutes: Mapped[int] = mapped_column(Integer, default=0)
    performed_by: Mapped[str] = mapped_column(String(120), nullable=False, default="engineer")
