"""Time normalization for Phase 1 (PHASE1_IMPLEMENTATION_PLAN.md).

Scoped down deliberately, per the plan's Decisions: a declared default timezone per source,
used to resolve which local calendar day a UTC instant falls in. No canonical table carries an
explicit per-observation offset today, so there is nothing yet to prefer over the declared
default -- when a source someday does provide one, resolving it happens before calling
`local_day_of` (by adjusting the instant passed in), not inside this module. Full
location-derived zone resolution (the architecture doc's stated ambition) is deferred to a
later phase.

This is the one place source-timezone resolution happens, so `measure.coverage`'s day buckets
and any future as-of-style day reasoning agree on the same calendar day for the same instant.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Phase 0's original convention for a source with no declared contract -- falling back rather
# than raising keeps every existing UTC-day computation (source_activity_daily included)
# meaningful for a source nobody has declared a contract for yet.
FALLBACK_TIMEZONE = "UTC"


def source_timezone(connection: Connection, source: str) -> str:
    """The declared declare.source_contract.default_timezone for a source, or FALLBACK_TIMEZONE
    when no contract has been declared.
    """
    row = connection.execute(
        text("SELECT default_timezone FROM declare.source_contract WHERE source = :source"),
        {"source": source},
    ).first()
    return row.default_timezone if row is not None else FALLBACK_TIMEZONE


def local_day_of(instant: datetime, timezone_name: str) -> date:
    """The local calendar day an instant falls in, per timezone_name."""
    return instant.astimezone(ZoneInfo(timezone_name)).date()


def local_day_bounds_utc(local_day: date, timezone_name: str) -> tuple[datetime, datetime]:
    """The [start, end) UTC instants covering one local calendar day in timezone_name -- the
    inverse of local_day_of, for building UTC-bound query parameters from a local day argument.
    """
    tz = ZoneInfo(timezone_name)
    start_local = datetime(local_day.year, local_day.month, local_day.day, tzinfo=tz)
    return start_local.astimezone(UTC), (start_local + timedelta(days=1)).astimezone(UTC)
