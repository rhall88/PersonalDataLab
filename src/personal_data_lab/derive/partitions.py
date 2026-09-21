from __future__ import annotations

from datetime import date, timedelta

GLOBAL_PARTITION_KEY = "GLOBAL"
UNDATED_SUFFIX = "undated"


def month_key(source: str, day: date) -> str:
    return f"{source}:{day:%Y-%m}"


def undated_key(source: str) -> str:
    return f"{source}:{UNDATED_SUFFIX}"


def parse_local_key(key: str) -> tuple[str, date | None, date | None]:
    """Split a `source:YYYY-MM` or `source:undated` key into (source, month_start, month_end),
    where month_end is the exclusive first day of the following month. Returns (source, None,
    None) for an undated key -- there is no day range to compute over.
    """
    source, _, suffix = key.partition(":")
    if not suffix:
        raise ValueError(f"malformed partition key (no ':'): {key!r}")
    if suffix == UNDATED_SUFFIX:
        return source, None, None
    year, month = int(suffix[:4]), int(suffix[5:7])
    start = date(year, month, 1)
    return source, start, _add_month(start)


def _add_month(day: date) -> date:
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


def _subtract_month(day: date) -> date:
    if day.month == 1:
        return date(day.year - 1, 12, 1)
    return date(day.year, day.month - 1, 1)


def halo_months(key: str, lookback_days: int, lookahead_days: int) -> tuple[str, ...]:
    """Expand a changed LOCAL month partition into every HALO month partition whose window
    could have been affected by it.

    A downstream HALO day d_down depends on upstream days [d_down - lookback, d_down + lookahead].
    So a change anywhere in upstream month M can affect a downstream day d_down whenever
    d_down in [first(M) - lookahead, last(M) + lookback]. This returns every month key that
    range touches. With lookback=6, lookahead=0 (source_activity_7d), a change in month M
    affects M itself and M+1 (since day 1..6 of M+1 look back into the tail of M) -- exercising
    the halo propagation across a month boundary that the Phase 0 plan calls for.

    An undated key has no day range and no halo neighbors -- it only ever affects itself.
    """
    source, start, end = parse_local_key(key)
    if start is None:
        return (key,)
    last_day_of_month = end - timedelta(days=1)
    window_start = start - timedelta(days=lookahead_days)
    window_end = last_day_of_month + timedelta(days=lookback_days)

    months: set[str] = set()
    cursor = date(window_start.year, window_start.month, 1)
    while cursor <= window_end:
        months.add(month_key(source, cursor))
        cursor = _add_month(cursor)
    return tuple(sorted(months))


def interval_to_days(value: timedelta | str | None) -> int:
    """Normalize a derive.deriver.lookback_halo/lookahead_halo value to whole days.

    psycopg maps Postgres `interval` columns to `datetime.timedelta` on read, so that's the
    common case; a plain string ('0' or 'N days') is also accepted for the values this package
    writes itself before a round-trip through the database. Not a general interval parser.
    """
    if value is None:
        return 0
    if isinstance(value, timedelta):
        return value.days
    if value in ("0", "00:00:00"):
        return 0
    parts = value.split()
    if len(parts) == 2 and parts[1] in ("day", "days"):
        return int(parts[0])
    raise ValueError(f"unsupported halo interval for Phase 0: {value!r}")
