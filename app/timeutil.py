"""Datetime helpers.

SQLite does not store timezone information, so a column declared
``DateTime(timezone=True)`` still comes back *naive* on SQLite while Postgres
returns it *aware*. Any arithmetic between the two raises
``TypeError: can't subtract offset-naive and offset-aware datetimes``.

Everything in this app is UTC by contract, so we normalise on read.
"""

from __future__ import annotations

from datetime import UTC, datetime


def as_utc(value: datetime) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime.

    Naive datetimes are assumed to already be UTC (which is what we always
    write), so they are simply stamped rather than shifted.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def days_between(later: datetime, earlier: datetime) -> float:
    """Whole days from ``earlier`` to ``later``, timezone-safe."""
    return (as_utc(later) - as_utc(earlier)).total_seconds() / 86400.0
