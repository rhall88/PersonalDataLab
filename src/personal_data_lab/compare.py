"""Phase 3's `compare` command (PHASE3_IMPLEMENTATION_PLAN.md): a ranked change table between
two date ranges over measure.metric_daily.

DuckDB queries rows already fetched from Postgres rather than using its own postgres_scanner
extension (Decision 7): that extension needs a first-run network download, and this project
otherwise has zero non-Postgres runtime dependencies and no other place assumes outbound network
access at query time. DuckDB still does the real analytical work here -- the per-period
aggregation and the ranking join -- Postgres's derive/measure schemas remain the sole source of
truth.

A metric's known_days for a period is the count of measure.metric_daily rows it actually has,
never assumed equal to the period's calendar length: Decision 4/5's "no row = unknown" convention
means a period with poor source coverage is visibly different from one with genuinely low
activity, instead of both collapsing into the same low number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb
from sqlalchemy import text
from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class DateRange:
    start: date
    end: date  # exclusive

    @property
    def total_days(self) -> int:
        return (self.end - self.start).days


class DateRangeError(ValueError):
    """Raised when a --focal/--baseline argument can't be parsed as START:END."""


def parse_date_range(spec: str) -> DateRange:
    """Parse a "YYYY-MM-DD:YYYY-MM-DD" spec into a DateRange with an exclusive end."""
    parts = spec.split(":")
    if len(parts) != 2:
        raise DateRangeError(f"expected START:END, got {spec!r}")
    try:
        start = date.fromisoformat(parts[0].strip())
        end = date.fromisoformat(parts[1].strip())
    except ValueError as error:
        raise DateRangeError(f"expected YYYY-MM-DD dates in {spec!r}: {error}") from None
    if end <= start:
        raise DateRangeError(f"end must be after start in {spec!r}")
    return DateRange(start=start, end=end)


def _fetch_metric_daily(
    engine: Engine, period: DateRange, metric_names: tuple[str, ...] | None
) -> list[tuple[str, date, float]]:
    query = (
        "SELECT mdf.name AS metric_name, md.bucket_start, md.value "
        "FROM measure.metric_daily md "
        "JOIN derive.metric_def mdf ON mdf.id = md.metric_id "
        "WHERE md.bucket_start >= :start AND md.bucket_start < :end"
    )
    params: dict = {"start": period.start, "end": period.end}
    if metric_names:
        query += " AND mdf.name = ANY(:names)"
        params["names"] = list(metric_names)
    with engine.connect() as connection:
        rows = connection.execute(text(query), params).all()
    return [(row.metric_name, row.bucket_start, float(row.value)) for row in rows]


def _load_period_table(con: duckdb.DuckDBPyConnection, table: str, rows: list[tuple]) -> None:
    con.execute(f"CREATE TABLE {table} (metric_name TEXT, bucket_start DATE, value DOUBLE)")
    if rows:
        con.executemany(f"INSERT INTO {table} VALUES (?, ?, ?)", rows)


def compare_periods(
    engine: Engine,
    *,
    focal: DateRange,
    baseline: DateRange,
    metric_names: tuple[str, ...] | None = None,
) -> list[dict]:
    """Rank every metric by absolute change between `focal` and `baseline`.

    Returns one dict per metric with focal/baseline values, absolute and percent change, and
    each period's known/total day counts. A metric absent from a period entirely (zero
    measure.metric_daily rows in that window) reports a None value for that period rather than 0
    -- "unknown never renders as 0" holds at the compare layer too, not just at derivation time.
    """
    focal_rows = _fetch_metric_daily(engine, focal, metric_names)
    baseline_rows = _fetch_metric_daily(engine, baseline, metric_names)

    con = duckdb.connect(":memory:")
    _load_period_table(con, "focal", focal_rows)
    _load_period_table(con, "baseline", baseline_rows)

    result = con.execute(
        """
        SELECT
            COALESCE(f.metric_name, b.metric_name) AS metric_name,
            f.total AS focal_value, f.known_days AS focal_known_days,
            b.total AS baseline_value, b.known_days AS baseline_known_days
        FROM
            (SELECT metric_name, SUM(value) AS total, COUNT(*) AS known_days
             FROM focal GROUP BY metric_name) f
        FULL OUTER JOIN
            (SELECT metric_name, SUM(value) AS total, COUNT(*) AS known_days
             FROM baseline GROUP BY metric_name) b
            ON f.metric_name = b.metric_name
        ORDER BY ABS(COALESCE(f.total, 0) - COALESCE(b.total, 0)) DESC, metric_name
        """
    ).fetchall()
    con.close()

    ranked: list[dict] = []
    for metric_name, focal_value, focal_known_days, baseline_value, baseline_known_days in result:
        absolute_change = (
            None
            if focal_value is None or baseline_value is None
            else focal_value - baseline_value
        )
        percent_change = (
            None
            if absolute_change is None or not baseline_value
            else (absolute_change / baseline_value) * 100
        )
        ranked.append(
            {
                "metric": metric_name,
                "focal_value": focal_value,
                "focal_known_days": focal_known_days or 0,
                "focal_total_days": focal.total_days,
                "baseline_value": baseline_value,
                "baseline_known_days": baseline_known_days or 0,
                "baseline_total_days": baseline.total_days,
                "absolute_change": absolute_change,
                "percent_change": percent_change,
            }
        )
    return ranked
