"""ISO 10816-3 vibration severity zones.

The standard classifies machine vibration by RMS velocity (mm/s) into four zones.
We use the displacement-equivalent thresholds commonly applied to rotating
machinery in the 15-75 kW band, adjusted by power class.

    Zone A — newly commissioned, good
    Zone B — acceptable for unrestricted long-term operation
    Zone C — unsatisfactory, plan maintenance
    Zone D — damage may occur, act now
"""

from __future__ import annotations

from dataclasses import dataclass

# Upper bound of each zone in mm/s RMS, per power class.
# Index 0 is unused so power_class maps directly.
_ZONE_BOUNDS: dict[int, tuple[float, float, float]] = {
    1: (1.8, 4.5, 11.2),  # small machines, < 15 kW
    2: (2.8, 7.1, 18.0),  # medium machines, 15-75 kW
    3: (3.5, 11.0, 28.0),  # large machines, > 75 kW
}

ZONE_PENALTY: dict[str, float] = {"A": 0.0, "B": 8.0, "C": 28.0, "D": 55.0}


@dataclass(frozen=True)
class VibrationVerdict:
    zone: str
    penalty: float
    limit_upper: float
    description: str


def classify_vibration(vibration_rms: float, power_class: int = 2) -> VibrationVerdict:
    """Map an RMS vibration reading to an ISO 10816-3 zone.

    Args:
        vibration_rms: measured velocity in mm/s RMS. Must be >= 0.
        power_class: 1 (small), 2 (medium), 3 (large). Unknown values fall back to 2.

    Raises:
        ValueError: if vibration_rms is negative or not finite.
    """
    if vibration_rms != vibration_rms or vibration_rms in (float("inf"), float("-inf")):
        raise ValueError("vibration_rms must be a finite number")
    if vibration_rms < 0:
        raise ValueError("vibration_rms cannot be negative")

    bounds = _ZONE_BOUNDS.get(power_class, _ZONE_BOUNDS[2])
    a_max, b_max, c_max = bounds

    if vibration_rms <= a_max:
        zone, limit, desc = "A", a_max, "Good — as commissioned"
    elif vibration_rms <= b_max:
        zone, limit, desc = "B", b_max, "Acceptable for continuous operation"
    elif vibration_rms <= c_max:
        zone, limit, desc = "C", c_max, "Unsatisfactory — schedule maintenance"
    else:
        zone, limit, desc = "D", c_max, "Severe — damage likely, act now"

    return VibrationVerdict(
        zone=zone,
        penalty=ZONE_PENALTY[zone],
        limit_upper=limit,
        description=desc,
    )
