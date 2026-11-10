"""Alert severity rules — pure functions over a health result."""

from __future__ import annotations

from dataclasses import dataclass

from app.engine.health import HealthResult

WARNING_THRESHOLD = 65.0
CRITICAL_THRESHOLD = 40.0


@dataclass(frozen=True)
class AlertDecision:
    should_alert: bool
    severity: str  # "info" | "warning" | "critical"
    title: str
    detail: str


def decide_alert(machine_name: str, result: HealthResult) -> AlertDecision:
    """Turn a health result into an alert decision, or a no-op."""
    health = result.health_index
    notes = result.breakdown.notes
    detail = "; ".join(notes) if notes else "No individual penalty exceeded its threshold."

    if health < CRITICAL_THRESHOLD:
        return AlertDecision(
            should_alert=True,
            severity="critical",
            title=f"{machine_name}: health {health:.0f}/100 — intervention required",
            detail=(
                f"Health index {health:.0f} is below the critical threshold of "
                f"{CRITICAL_THRESHOLD:.0f}. {detail}"
            ),
        )

    if health < WARNING_THRESHOLD:
        return AlertDecision(
            should_alert=True,
            severity="warning",
            title=f"{machine_name}: health {health:.0f}/100 — plan maintenance",
            detail=(
                f"Health index {health:.0f} is below the warning threshold of "
                f"{WARNING_THRESHOLD:.0f}. {detail}"
            ),
        )

    return AlertDecision(
        should_alert=False,
        severity="info",
        title=f"{machine_name}: healthy ({health:.0f}/100)",
        detail=detail,
    )


def priority_for_severity(severity: str) -> str:
    return {"critical": "urgent", "warning": "high", "info": "low"}.get(severity, "medium")
