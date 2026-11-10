"""Health index and remaining-useful-life estimation.

Everything here is deterministic: same inputs, same outputs, no clock, no randomness.
That is what lets the tests assert exact numbers and what lets an engineer reproduce
a score they disagree with.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.engine.vibration import classify_vibration

CRITICAL_THRESHOLD = 40.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class HealthBreakdown:
    """The four penalties that make up a health score, each with its reason."""

    vibration: float
    thermal: float
    current: float
    duty: float
    notes: list[str] = field(default_factory=list)

    @property
    def total_penalty(self) -> float:
        return self.vibration + self.thermal + self.current + self.duty


@dataclass(frozen=True)
class HealthResult:
    health_index: float
    breakdown: HealthBreakdown
    vibration_zone: str
    vibration_description: str

    @property
    def is_critical(self) -> bool:
        return self.health_index < CRITICAL_THRESHOLD


def thermal_penalty(temperature_c: float, baseline_temperature: float) -> float:
    """Rise above baseline, ignoring the first 5 °C of normal drift."""
    rise = temperature_c - baseline_temperature - 5.0
    return _clamp(rise * 1.6, 0.0, 35.0)


def current_penalty(current_a: float, baseline_current: float) -> float:
    """Deviation from baseline current, ignoring the first 10%."""
    if baseline_current <= 0:
        return 0.0
    deviation = abs(current_a - baseline_current) / baseline_current
    return _clamp((deviation - 0.10) * 120.0, 0.0, 20.0)


def duty_penalty(runtime_hours: float) -> float:
    """Wear accrues slowly with total running hours."""
    return _clamp(runtime_hours / 20_000.0 * 10.0, 0.0, 10.0)


def score_health(
    *,
    vibration_rms: float,
    temperature_c: float,
    current_a: float,
    runtime_hours: float,
    baseline_vibration: float,
    baseline_temperature: float,
    baseline_current: float,
    power_class: int = 2,
) -> HealthResult:
    """Combine four independent penalties into a 0-100 health index."""
    vib = classify_vibration(vibration_rms, power_class)
    thermal = thermal_penalty(temperature_c, baseline_temperature)
    current = current_penalty(current_a, baseline_current)
    duty = duty_penalty(runtime_hours)

    notes: list[str] = []
    if vib.zone in ("C", "D"):
        notes.append(
            f"Vibration {vibration_rms:.1f} mm/s is ISO zone {vib.zone} "
            f"(limit {vib.limit_upper:.1f}) — {vib.description.lower()}"
        )
    if thermal > 0:
        notes.append(
            f"Temperature {temperature_c:.1f} °C is "
            f"{temperature_c - baseline_temperature:.1f} °C over baseline"
        )
    if current > 0:
        notes.append(
            f"Current {current_a:.1f} A deviates "
            f"{abs(current_a - baseline_current) / baseline_current * 100:.0f}% from baseline"
        )
    if duty > 5:
        notes.append(f"{runtime_hours:,.0f} running hours — wear allowance mostly consumed")

    breakdown = HealthBreakdown(
        vibration=vib.penalty,
        thermal=round(thermal, 2),
        current=round(current, 2),
        duty=round(duty, 2),
        notes=notes,
    )
    health = _clamp(100.0 - breakdown.total_penalty, 0.0, 100.0)

    return HealthResult(
        health_index=round(health, 2),
        breakdown=breakdown,
        vibration_zone=vib.zone,
        vibration_description=vib.description,
    )


@dataclass(frozen=True)
class RulEstimate:
    """Remaining useful life, with the honesty flags attached."""

    days_remaining: float | None
    slope_per_day: float
    confidence: str  # "low" | "medium" | "high"
    sample_count: int
    r_squared: float
    reason: str


def _least_squares(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Return (slope, intercept, r_squared) for a simple linear fit."""
    n = len(xs)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0, 0.0

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    if ss_xx == 0:
        return 0.0, mean_y, 0.0

    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x

    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys, strict=True))
    r_squared = 0.0 if ss_tot == 0 else max(0.0, 1.0 - ss_res / ss_tot)

    return slope, intercept, r_squared


def estimate_rul(
    health_series: list[tuple[float, float]],
    *,
    critical_threshold: float = CRITICAL_THRESHOLD,
    window: int = 20,
) -> RulEstimate:
    """Estimate days until the health index crosses the critical threshold.

    Args:
        health_series: list of (days_ago, health_index), newest first.
        critical_threshold: health value considered end-of-useful-life.
        window: how many recent points to fit over.

    The fit is over days_ago ascending, so a *negative* slope means health is
    falling as time moves forward.
    """
    points = health_series[:window]
    if len(points) < 3:
        return RulEstimate(
            days_remaining=None,
            slope_per_day=0.0,
            confidence="low",
            sample_count=len(points),
            r_squared=0.0,
            reason="Not enough history — need at least 3 readings to fit a trend.",
        )

    # Convert to (x = -days_ago) so x increases with time.
    xs = [-d for d, _ in points]
    ys = [h for _, h in points]
    slope, _intercept, r_squared = _least_squares(xs, ys)

    current_health = ys[0]
    if slope >= -0.01:
        return RulEstimate(
            days_remaining=None,
            slope_per_day=round(slope, 3),
            confidence="medium" if r_squared > 0.5 else "low",
            sample_count=len(points),
            r_squared=round(r_squared, 3),
            reason="Health is flat or improving — no failure trend to extrapolate.",
        )

    days = (current_health - critical_threshold) / -slope
    days = max(0.0, days)

    if len(points) >= 12 and r_squared >= 0.75:
        confidence = "high"
    elif len(points) >= 6 and r_squared >= 0.45:
        confidence = "medium"
    else:
        confidence = "low"

    return RulEstimate(
        days_remaining=round(days, 1),
        slope_per_day=round(slope, 3),
        confidence=confidence,
        sample_count=len(points),
        r_squared=round(r_squared, 3),
        reason=(
            f"Health falling {abs(slope):.2f} points/day over {len(points)} readings "
            f"(R²={r_squared:.2f})."
        ),
    )
