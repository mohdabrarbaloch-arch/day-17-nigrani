"""Tests for the vibration classifier."""

from __future__ import annotations

import pytest

from app.engine.vibration import ZONE_PENALTY, classify_vibration


def test_zone_a_for_low_vibration():
    v = classify_vibration(1.5, power_class=2)
    assert v.zone == "A"
    assert v.penalty == 0.0


def test_zone_b_boundary_is_inclusive():
    # 2.8 is exactly the A/B boundary for class 2 — still A.
    assert classify_vibration(2.8, power_class=2).zone == "A"
    assert classify_vibration(2.81, power_class=2).zone == "B"


def test_zone_c_and_d():
    assert classify_vibration(10.0, power_class=2).zone == "C"
    assert classify_vibration(25.0, power_class=2).zone == "D"


def test_power_class_changes_thresholds():
    # 3.0 mm/s is zone B for a small machine but zone A for a large one.
    assert classify_vibration(3.0, power_class=1).zone == "B"
    assert classify_vibration(3.0, power_class=3).zone == "A"


def test_unknown_power_class_falls_back_to_medium():
    assert classify_vibration(5.0, power_class=99).zone == classify_vibration(5.0, 2).zone


def test_negative_vibration_rejected():
    with pytest.raises(ValueError):
        classify_vibration(-1.0)


def test_nan_rejected():
    with pytest.raises(ValueError):
        classify_vibration(float("nan"))


def test_penalties_are_monotonic():
    assert ZONE_PENALTY["A"] < ZONE_PENALTY["B"] < ZONE_PENALTY["C"] < ZONE_PENALTY["D"]
