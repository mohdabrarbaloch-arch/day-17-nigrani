"""Nigrani's predictive-maintenance engine.

Everything here is deterministic arithmetic. No trained weights, no black box.
Given the same readings you get the same health index, the same remaining
useful life, and the same alert — which is the whole point: a maintenance
engineer has to be able to argue with the number, not just trust it.

The model is deliberately simple and documented:

1. Each sensor channel is normalised against the machine's own nominal and
   critical values, producing a 0..1 sub-score per channel.
2. The health index is a weighted mean of those sub-scores, with the worst
   channel pulling the result down (a machine is only as healthy as its
   weakest bearing).
3. Degradation rate is the slope of a least-squares line through the recent
   health history, expressed in health-points per hour.
4. Remaining useful life is the time until health crosses the failure
   threshold at that slope. If the machine is not degrading, RUL is None
   (unknown, not infinite) — we refuse to invent a number.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# Health below this is treated as failed for RUL purposes.
FAILURE_THRESHOLD = 30.0

# Alert bands. Ordered high -> low so the first match wins.
ALERT_BANDS: tuple[tuple[float, str, str], ...] = (
    (30.0, "emergency", "Machine is at or past the failure threshold. Stop it."),
    (50.0, "critical", "Health is in the critical band. Schedule intervention now."),
    (70.0, "warning", "Health is degrading. Plan maintenance this week."),
)

# A machine losing more than this many health points per hour is degrading
# fast enough to matter even if its absolute health still looks fine.
DEGRADATION_ALERT_PER_HOUR = 1.5

# How many recent readings feed the trend line.
TREND_WINDOW = 12


@dataclass(frozen=True)
class ChannelScore:
    """One sensor channel's contribution to the health index."""

    name: str
    value: float
    nominal: float
    critical: float
    score: float  # 0..1, 1 = as new, 0 = at or past critical
    weight: float

    @property
    def contribution(self) -> float:
        return self.score * self.weight


@dataclass(frozen=True)
class HealthReport:
    """The full, explainable verdict for one machine at one moment."""

    health: float
    channels: tuple[ChannelScore, ...]
    worst_channel: str
    degradation_per_hour: float | None
    rul_hours: float | None
    severity: str | None
    reason: str | None

    @property
    def is_alerting(self) -> bool:
        return self.severity is not None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def score_channel(name: str, value: float, nominal: float, critical: float, weight: float) -> ChannelScore:
    """Normalise one channel to 0..1.

    A reading at or below nominal scores 1.0. A reading at or above critical
    scores 0.0. In between it is linear. If critical <= nominal the channel is
    misconfigured and we score it 0 rather than divide by zero — a broken
    configuration should look broken, not healthy.
    """
    span = critical - nominal
    if span <= 0:
        return ChannelScore(name, value, nominal, critical, 0.0, weight)
    score = _clamp(1.0 - (value - nominal) / span)
    return ChannelScore(name, value, nominal, critical, score, weight)


def health_index(channels: Sequence[ChannelScore]) -> float:
    """Weighted mean, then pulled toward the worst channel.

    A plain weighted mean lets three healthy channels hide one dying bearing.
    We blend 70% weighted mean with 30% worst channel so a single bad channel
    can never be fully masked, while still rewarding overall condition.
    """
    if not channels:
        return 0.0
    total_weight = sum(c.weight for c in channels) or 1.0
    weighted = sum(c.contribution for c in channels) / total_weight
    worst = min(c.score for c in channels)
    blended = 0.7 * weighted + 0.3 * worst
    return round(_clamp(blended) * 100.0, 2)


def least_squares_slope(points: Sequence[tuple[float, float]]) -> float | None:
    """Slope of y over x, in y-units per x-unit. None if under-determined."""
    n = len(points)
    if n < 3:
        return None
    mean_x = sum(p[0] for p in points) / n
    mean_y = sum(p[1] for p in points) / n
    denom = sum((p[0] - mean_x) ** 2 for p in points)
    if denom == 0:
        return None
    numer = sum((p[0] - mean_x) * (p[1] - mean_y) for p in points)
    return numer / denom


def _naive_utc(ts: datetime) -> datetime:
    """Normalise a timestamp to naive UTC.

    SQLite hands back naive datetimes while a freshly-flushed row still carries
    the aware datetime we inserted. Subtracting one from the other raises
    TypeError, so every timestamp is normalised before any arithmetic.
    """
    if ts.tzinfo is None:
        return ts
    return ts.astimezone(UTC).replace(tzinfo=None)


def degradation_rate(history: Sequence[tuple[datetime, float]]) -> float | None:
    """Health points lost per hour, positive means degrading.

    `history` is (timestamp, health) oldest-first. We convert timestamps to
    hours relative to the first sample so the slope is directly in points/hour.
    """
    if len(history) < 3:
        return None
    origin = _naive_utc(history[0][0])
    points = [((_naive_utc(ts) - origin).total_seconds() / 3600.0, health) for ts, health in history]
    slope = least_squares_slope(points)
    if slope is None:
        return None
    # slope is health per hour; degradation is its negation.
    return round(-slope, 4)


def remaining_useful_life(health: float, rate_per_hour: float | None) -> float | None:
    """Hours until health reaches FAILURE_THRESHOLD at the current rate.

    Returns None when the machine is not degrading (rate <= 0) or when the
    rate is unknown. We do not report "infinite life" — we report "unknown",
    because a flat trend over twelve samples is not a promise about next month.
    """
    if rate_per_hour is None or rate_per_hour <= 0:
        return None
    if health <= FAILURE_THRESHOLD:
        return 0.0
    return round((health - FAILURE_THRESHOLD) / rate_per_hour, 2)


def classify(health: float, rate_per_hour: float | None) -> tuple[str | None, str | None]:
    """Pick the alert severity and the human reason for it."""
    for threshold, severity, reason in ALERT_BANDS:
        if health < threshold:
            return severity, reason
    if rate_per_hour is not None and rate_per_hour > DEGRADATION_ALERT_PER_HOUR:
        return (
            "warning",
            f"Health is still {health:.0f} but falling {rate_per_hour:.2f} points/hour — "
            "faster than a healthy machine should.",
        )
    return None, None


def evaluate(
    readings: Sequence[tuple[datetime, float, float, float]],
    nominal: tuple[float, float, float],
    critical: tuple[float, float, float],
    weights: tuple[float, float, float] = (0.5, 0.3, 0.2),
) -> HealthReport:
    """Evaluate a machine from its reading history.

    `readings` is (timestamp, vibration_rms, temperature_c, current_a),
    oldest-first. The last reading is "now"; earlier ones feed the trend.
    """
    if not readings:
        raise ValueError("cannot evaluate a machine with no readings")

    ts, vib, temp, current = readings[-1]
    channels = (
        score_channel("vibration", vib, nominal[0], critical[0], weights[0]),
        score_channel("temperature", temp, nominal[1], critical[1], weights[1]),
        score_channel("current", current, nominal[2], critical[2], weights[2]),
    )
    health = health_index(channels)
    worst = min(channels, key=lambda c: c.score)

    window = readings[-TREND_WINDOW:]
    history = [(r[0], _health_of(r, nominal, critical, weights)) for r in window]
    rate = degradation_rate(history)
    rul = remaining_useful_life(health, rate)
    severity, reason = classify(health, rate)

    return HealthReport(
        health=health,
        channels=channels,
        worst_channel=worst.name,
        degradation_per_hour=rate,
        rul_hours=rul,
        severity=severity,
        reason=reason,
    )


def _health_of(
    reading: tuple[datetime, float, float, float],
    nominal: tuple[float, float, float],
    critical: tuple[float, float, float],
    weights: tuple[float, float, float],
) -> float:
    _, vib, temp, current = reading
    channels = (
        score_channel("vibration", vib, nominal[0], critical[0], weights[0]),
        score_channel("temperature", temp, nominal[1], critical[1], weights[1]),
        score_channel("current", current, nominal[2], critical[2], weights[2]),
    )
    return health_index(channels)


def project_health(health: float, rate_per_hour: float | None, hours: float) -> float:
    """Where health lands after `hours`, clamped to 0..100. Used for the chart."""
    if rate_per_hour is None:
        return health
    return round(_clamp(health - rate_per_hour * hours, 0.0, 100.0), 2)


def summarise_fleet(reports: Iterable[HealthReport]) -> dict[str, int]:
    """Count machines per severity band, for the dashboard header."""
    counts = {"healthy": 0, "warning": 0, "critical": 0, "emergency": 0}
    for report in reports:
        counts[report.severity or "healthy"] += 1
    return counts


def utcnow() -> datetime:
    return datetime.now(UTC)


def hours_ago(hours: float) -> datetime:
    return utcnow() - timedelta(hours=hours)
