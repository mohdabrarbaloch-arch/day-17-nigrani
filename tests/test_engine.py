"""Tests for the deterministic engine.

These are the tests that matter: if the health index or the RUL maths drifts,
a maintenance team makes a wrong call. Everything else in the app is plumbing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app import engine

T0 = datetime(2026, 11, 11, 0, 0, tzinfo=UTC)

NOMINAL = (2.0, 45.0, 12.0)
CRITICAL = (11.0, 95.0, 22.0)


def series(values: list[float], step_hours: float = 1.0) -> list[tuple[datetime, float, float, float]]:
    """Build readings where vibration follows `values` and the rest stay nominal."""
    return [(T0 + timedelta(hours=i * step_hours), v, 45.0, 12.0) for i, v in enumerate(values)]


# ── channel scoring ──────────────────────────────────────────────


def test_channel_at_nominal_scores_one():
    c = engine.score_channel("vibration", 2.0, 2.0, 11.0, 0.5)
    assert c.score == pytest.approx(1.0)


def test_channel_at_critical_scores_zero():
    c = engine.score_channel("vibration", 11.0, 2.0, 11.0, 0.5)
    assert c.score == pytest.approx(0.0)


def test_channel_midpoint_scores_half():
    c = engine.score_channel("vibration", 6.5, 2.0, 11.0, 0.5)
    assert c.score == pytest.approx(0.5)


def test_channel_below_nominal_is_clamped_to_one():
    c = engine.score_channel("vibration", 0.0, 2.0, 11.0, 0.5)
    assert c.score == pytest.approx(1.0)


def test_channel_past_critical_is_clamped_to_zero():
    c = engine.score_channel("vibration", 50.0, 2.0, 11.0, 0.5)
    assert c.score == pytest.approx(0.0)


def test_misconfigured_channel_scores_zero_not_crash():
    """critical <= nominal is a config error; it must look broken, not healthy."""
    c = engine.score_channel("vibration", 5.0, 10.0, 10.0, 0.5)
    assert c.score == 0.0


# ── health index ─────────────────────────────────────────────────


def test_health_index_all_nominal_is_100():
    channels = [
        engine.score_channel("vibration", 2.0, 2.0, 11.0, 0.5),
        engine.score_channel("temperature", 45.0, 45.0, 95.0, 0.3),
        engine.score_channel("current", 12.0, 12.0, 22.0, 0.2),
    ]
    assert engine.health_index(channels) == pytest.approx(100.0)


def test_health_index_all_critical_is_zero():
    channels = [
        engine.score_channel("vibration", 11.0, 2.0, 11.0, 0.5),
        engine.score_channel("temperature", 95.0, 45.0, 95.0, 0.3),
        engine.score_channel("current", 22.0, 12.0, 22.0, 0.2),
    ]
    assert engine.health_index(channels) == pytest.approx(0.0)


def test_one_dead_channel_cannot_be_hidden_by_healthy_ones():
    """The whole point of the worst-channel pull.

    Three channels: one at zero, two at one. A plain weighted mean with weights
    .5/.3/.2 gives 0.5. The blend must land below that.
    """
    channels = [
        engine.score_channel("vibration", 11.0, 2.0, 11.0, 0.5),  # dead
        engine.score_channel("temperature", 45.0, 45.0, 95.0, 0.3),  # perfect
        engine.score_channel("current", 12.0, 12.0, 22.0, 0.2),  # perfect
    ]
    health = engine.health_index(channels)
    assert health < 50.0
    # 0.7 * 0.5 + 0.3 * 0.0 = 0.35
    assert health == pytest.approx(35.0)


def test_health_index_empty_is_zero():
    assert engine.health_index([]) == 0.0


# ── trend ────────────────────────────────────────────────────────


def test_slope_of_flat_line_is_zero():
    points = [(0.0, 90.0), (1.0, 90.0), (2.0, 90.0)]
    assert engine.least_squares_slope(points) == pytest.approx(0.0)


def test_slope_of_perfect_ramp():
    points = [(0.0, 100.0), (1.0, 98.0), (2.0, 96.0)]
    assert engine.least_squares_slope(points) == pytest.approx(-2.0)


def test_slope_needs_three_points():
    assert engine.least_squares_slope([(0.0, 1.0), (1.0, 2.0)]) is None


def test_slope_with_identical_x_is_none():
    assert engine.least_squares_slope([(1.0, 1.0), (1.0, 2.0), (1.0, 3.0)]) is None


def test_degradation_rate_is_positive_when_health_falls():
    history = [
        (T0, 90.0),
        (T0 + timedelta(hours=1), 88.0),
        (T0 + timedelta(hours=2), 86.0),
    ]
    rate = engine.degradation_rate(history)
    assert rate == pytest.approx(2.0)


def test_degradation_rate_is_negative_when_health_improves():
    history = [
        (T0, 60.0),
        (T0 + timedelta(hours=1), 65.0),
        (T0 + timedelta(hours=2), 70.0),
    ]
    assert engine.degradation_rate(history) == pytest.approx(-5.0)


def test_degradation_rate_needs_three_samples():
    assert engine.degradation_rate([(T0, 90.0), (T0 + timedelta(hours=1), 88.0)]) is None


# ── remaining useful life ────────────────────────────────────────


def test_rul_is_hours_to_threshold():
    # 80 health, losing 2/hour, floor at 30 → (80-30)/2 = 25 hours
    assert engine.remaining_useful_life(80.0, 2.0) == pytest.approx(25.0)


def test_rul_is_none_when_not_degrading():
    """A flat trend is not a promise of infinite life — we say unknown."""
    assert engine.remaining_useful_life(80.0, 0.0) is None
    assert engine.remaining_useful_life(80.0, -1.0) is None


def test_rul_is_none_when_rate_unknown():
    assert engine.remaining_useful_life(80.0, None) is None


def test_rul_is_zero_when_already_past_threshold():
    assert engine.remaining_useful_life(20.0, 2.0) == pytest.approx(0.0)


# ── classification ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "health,expected",
    [
        (10.0, "emergency"),
        (29.9, "emergency"),
        (30.0, "critical"),
        (49.9, "critical"),
        (50.0, "warning"),
        (69.9, "warning"),
        (70.0, None),
        (100.0, None),
    ],
)
def test_classify_bands(health, expected):
    severity, _ = engine.classify(health, None)
    assert severity == expected


def test_fast_degradation_alerts_even_when_health_is_fine():
    severity, reason = engine.classify(85.0, 2.0)
    assert severity == "warning"
    assert "points/hour" in reason


def test_slow_degradation_at_good_health_is_quiet():
    severity, _ = engine.classify(85.0, 0.2)
    assert severity is None


# ── end to end ───────────────────────────────────────────────────


def test_evaluate_healthy_machine():
    readings = series([2.0, 2.05, 2.02, 2.01, 2.03, 2.0])
    report = engine.evaluate(readings, NOMINAL, CRITICAL)
    assert report.health > 95.0
    assert report.severity is None
    assert report.worst_channel == "vibration"


def test_evaluate_degrading_machine_raises_warning():
    readings = series([2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    report = engine.evaluate(readings, NOMINAL, CRITICAL)
    assert report.health < 70.0
    assert report.severity in {"warning", "critical"}
    assert report.degradation_per_hour is not None
    assert report.degradation_per_hour > 0


def test_one_dead_channel_lands_in_critical_not_emergency():
    """Pins the documented blend.

    Vibration at critical, temperature and current perfect:
      weighted = .5*0 + .3*1 + .2*1 = 0.5
      blended  = .7*0.5 + .3*0.0 = 0.35  -> 35 health -> "critical"
    A single dead channel is serious but not the same as a machine that is
    dead on every channel. That distinction is the reason for the 70/30 blend.
    """
    readings = series([10.5, 10.7, 10.8, 10.9, 11.0, 11.2])
    report = engine.evaluate(readings, NOMINAL, CRITICAL)
    assert report.health == pytest.approx(35.0, abs=1.0)
    assert report.severity == "critical"


def test_evaluate_machine_dead_on_every_channel_is_emergency():
    readings = [(T0 + timedelta(hours=i), 11.0, 95.0, 22.0) for i in range(6)]
    report = engine.evaluate(readings, NOMINAL, CRITICAL)
    assert report.health == pytest.approx(0.0)
    assert report.severity == "emergency"


def test_evaluate_reports_worst_channel_correctly():
    # Temperature is the only channel that is bad.
    readings = [(T0 + timedelta(hours=i), 2.0, 94.0, 12.0) for i in range(5)]
    report = engine.evaluate(readings, NOMINAL, CRITICAL)
    assert report.worst_channel == "temperature"


def test_evaluate_rejects_empty_readings():
    with pytest.raises(ValueError):
        engine.evaluate([], NOMINAL, CRITICAL)


def test_evaluate_is_deterministic():
    readings = series([2.0, 3.5, 5.0, 6.5, 8.0])
    a = engine.evaluate(readings, NOMINAL, CRITICAL)
    b = engine.evaluate(readings, NOMINAL, CRITICAL)
    assert a.health == b.health
    assert a.rul_hours == b.rul_hours
    assert a.degradation_per_hour == b.degradation_per_hour


def test_project_health_clamps_at_zero():
    assert engine.project_health(40.0, 5.0, 100.0) == pytest.approx(0.0)


def test_project_health_flat_when_rate_unknown():
    assert engine.project_health(40.0, None, 100.0) == pytest.approx(40.0)


def test_summarise_fleet_counts_bands():
    healthy = engine.evaluate(series([2.0, 2.0, 2.0]), NOMINAL, CRITICAL)
    dead = engine.evaluate(
        [(T0 + timedelta(hours=i), 11.0, 95.0, 22.0) for i in range(3)],
        NOMINAL,
        CRITICAL,
    )
    counts = engine.summarise_fleet([healthy, dead])
    assert counts["healthy"] == 1
    assert counts["emergency"] == 1


def test_degradation_rate_tolerates_mixed_naive_and_aware_timestamps():
    """SQLite returns naive datetimes; a freshly-flushed row is aware.

    Mixing them used to raise TypeError. The engine normalises both to naive UTC.
    """
    aware = datetime(2026, 11, 11, 0, 0, tzinfo=UTC)
    naive = datetime(2026, 11, 11, 1, 0)
    history = [(aware, 90.0), (naive, 88.0), (datetime(2026, 11, 11, 2, 0), 86.0)]
    assert engine.degradation_rate(history) == pytest.approx(2.0)
