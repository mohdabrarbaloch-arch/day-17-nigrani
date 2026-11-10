"""Tests for the health index and RUL estimator."""

from __future__ import annotations

import pytest

from app.engine.health import (
    current_penalty,
    duty_penalty,
    estimate_rul,
    score_health,
    thermal_penalty,
)

BASELINE = {
    "baseline_vibration": 2.8,
    "baseline_temperature": 62.0,
    "baseline_current": 10.0,
    "power_class": 2,
}


def test_healthy_machine_scores_near_100():
    r = score_health(
        vibration_rms=2.0,
        temperature_c=63.0,
        current_a=10.2,
        runtime_hours=1000,
        **BASELINE,
    )
    assert r.health_index >= 99.0
    assert r.vibration_zone == "A"
    assert r.is_critical is False


def test_critical_machine_scores_low():
    r = score_health(
        vibration_rms=22.0,
        temperature_c=95.0,
        current_a=14.0,
        runtime_hours=19_000,
        **BASELINE,
    )
    assert r.health_index < 40
    assert r.is_critical is True
    assert r.vibration_zone == "D"


def test_thermal_penalty_ignores_first_five_degrees():
    assert thermal_penalty(66.0, 62.0) == 0.0
    assert thermal_penalty(67.0, 62.0) == pytest.approx(0.0)
    assert thermal_penalty(72.0, 62.0) == pytest.approx(8.0)


def test_thermal_penalty_is_capped():
    assert thermal_penalty(200.0, 62.0) == 35.0


def test_current_penalty_ignores_first_ten_percent():
    assert current_penalty(10.9, 10.0) == 0.0
    assert current_penalty(12.0, 10.0) == pytest.approx(12.0)


def test_duty_penalty_scales_with_hours():
    assert duty_penalty(0) == 0.0
    assert duty_penalty(10_000) == pytest.approx(5.0)
    assert duty_penalty(50_000) == 10.0  # capped


def test_breakdown_notes_explain_the_score():
    r = score_health(
        vibration_rms=12.0,
        temperature_c=80.0,
        current_a=13.0,
        runtime_hours=15_000,
        **BASELINE,
    )
    joined = " ".join(r.breakdown.notes)
    assert "ISO zone C" in joined
    assert "over baseline" in joined


def test_health_never_goes_negative():
    r = score_health(
        vibration_rms=100.0,
        temperature_c=300.0,
        current_a=100.0,
        runtime_hours=100_000,
        **BASELINE,
    )
    assert r.health_index == 0.0


# ── RUL ───────────────────────────────────────────────────────────────────────
def test_rul_needs_three_points():
    r = estimate_rul([(0, 80.0), (1, 78.0)])
    assert r.days_remaining is None
    assert r.confidence == "low"
    assert "Not enough history" in r.reason


def test_rul_detects_flat_trend():
    series = [(i, 80.0) for i in range(10)]
    r = estimate_rul(series)
    assert r.days_remaining is None
    assert "flat or improving" in r.reason


def test_rul_extrapolates_a_clean_decline():
    # Health drops exactly 2 points per day from 80 → hits 40 in 20 days.
    series = [(i, 80.0 + 2.0 * i) for i in range(10)]
    r = estimate_rul(series)
    assert r.days_remaining == pytest.approx(20.0, abs=0.5)
    assert r.slope_per_day == pytest.approx(-2.0, abs=0.01)
    assert r.confidence in ("medium", "high")


def test_rul_confidence_is_honest_about_noise():
    # Wildly noisy series should not claim high confidence.
    series = [(0, 80.0), (1, 40.0), (2, 90.0), (3, 30.0), (4, 85.0)]
    r = estimate_rul(series)
    assert r.confidence == "low"


def test_rul_never_returns_negative_days():
    series = [(i, 30.0 + 5.0 * i) for i in range(6)]
    r = estimate_rul(series)
    assert r.days_remaining is not None
    assert r.days_remaining >= 0.0
