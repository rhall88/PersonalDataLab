"""Phase 1's coverage deriver (PHASE1_IMPLEMENTATION_PLAN.md): per-source, per-day completeness
against each source's declared declare.source_contract, riding Phase 0's existing LOCAL
partition/change-feed/worker machinery unchanged -- no new invalidation logic.

Semantics (exit tests 2-3 in the plan): for each day within a source's declared covered_range
and each object_kind in its observable_kinds, `expected` is always 1 (a day/kind combination the
contract says should show something) and `observed` is the actual watched-row count for that
day, same precedent as source_activity_daily. `completeness` is 1.0 when observed >= 1 else 0.0
-- a coverage heatmap cell answers "did anything land that day", not an attempt to estimate a
true message count no independent oracle exists for. A day outside the declared covered_range,
or a source with no declared contract at all, gets no row: an undeclared period is silence, not
a fabricated gap (migration 011's own convention). `gap_reason`, when a gap is flagged, is
copied from the contract's own `gap_semantics` -- the contract's one declared explanation for
its internal gaps, not a per-day diagnosis.

Relationships are excluded (same reason changefeed.py excludes them from WATCHED_KINDS): they
have no occurred_at-equivalent column, so there is no day to bucket them by.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ...fingerprints import fingerprint_value
from ..changefeed import WATCHED_KINDS
from ..partitions import parse_local_key
from ..registry import ComputeResult, deriver
from ..time_norm import source_timezone

_KIND_LABELS: tuple[str, ...] = tuple(label for label, _obs, _id, _canon, _date in WATCHED_KINDS)
_KIND_TABLES: dict[str, tuple[str, str]] = {
    label: (canon_table, date_expr) for label, _obs, _id, canon_table, date_expr in WATCHED_KINDS
}


def _read_contract(connection: Connection, source: str):
    return connection.execute(
        text(
            "SELECT observable_kinds, covered_range_start, covered_range_end, gap_semantics, "
            "default_timezone, updated_at FROM declare.source_contract WHERE source = :source"
        ),
        {"source": source},
    ).first()


def _covered_days(
    month_start: date, month_end: date, range_start: date | None, range_end: date | None
) -> list[date]:
    """Days in [month_start, month_end) that also fall within the contract's covered_range,
    treating a NULL bound as unbounded on that side."""
    start = max(month_start, range_start) if range_start else month_start
    end = min(month_end, range_end + timedelta(days=1)) if range_end else month_end
    days: list[date] = []
    cursor = start
    while cursor < end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _clear_coverage(connection: Connection, partition_key: str) -> None:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        return  # the undated bucket never has coverage rows; bucket_start is NOT NULL
    connection.execute(
        text(
            "DELETE FROM measure.coverage WHERE source = :source "
            "AND bucket_start >= :start AND bucket_start < :end"
        ),
        {"source": source, "start": start, "end": end},
    )


@deriver(
    name="measure_coverage",
    partition_class="LOCAL",
    final_inference="COMPUTED",
    description=(
        "Per-source, per-day completeness against declare.source_contract: expected vs. "
        "observed watched-kind activity, day-grain, month is a query-side aggregation."
    ),
    output_relation="measure.coverage",
    clear_partition=_clear_coverage,
)
def measure_coverage(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    source, start, end = parse_local_key(partition_key)
    contract = _read_contract(connection, source)

    if start is None:
        return ComputeResult(input_fingerprint=fingerprint_value(["undated"]), rows=[])
    if contract is None:
        # No declared contract at all: nothing to compute (module docstring). Still
        # fingerprinted distinctly so a later contract-declare + derive-rebuild recomputes.
        return ComputeResult(input_fingerprint=fingerprint_value(["no-contract"]), rows=[])

    timezone_name = source_timezone(connection, source)
    observable = set(contract.observable_kinds)
    days = _covered_days(start, end, contract.covered_range_start, contract.covered_range_end)
    if not days:
        fp = fingerprint_value(["out-of-range", contract.updated_at.isoformat()])
        return ComputeResult(input_fingerprint=fp, rows=[])

    fingerprint_parts: list = []
    rows: list[dict] = []
    for kind in _KIND_LABELS:
        if kind not in observable:
            continue
        table, date_expr = _KIND_TABLES[kind]
        result = connection.execute(
            text(
                f"""
                SELECT id, canonical_revision,
                       ({date_expr} AT TIME ZONE :tz)::date AS day
                FROM {table}
                WHERE source = :source
                  AND lifecycle_state = 'active'
                  AND {date_expr} IS NOT NULL
                  AND ({date_expr} AT TIME ZONE :tz)::date >= :start
                  AND ({date_expr} AT TIME ZONE :tz)::date < :end
                ORDER BY id
                """
            ),
            {"source": source, "tz": timezone_name, "start": start, "end": end},
        ).all()
        fingerprint_parts.append([kind, [[r.id, r.canonical_revision] for r in result]])

        by_day: dict[date, int] = {}
        for row in result:
            by_day[row.day] = by_day.get(row.day, 0) + 1
        for day in days:
            observed = by_day.get(day, 0)
            completeness = 1.0 if observed >= 1 else 0.0
            rows.append(
                {
                    "source": source,
                    "bucket_start": day,
                    "grain": "day",
                    "object_kind": kind,
                    "expected": 1,
                    "observed": observed,
                    "completeness": completeness,
                    "gap_reason": None if observed >= 1 else contract.gap_semantics,
                    "computed_epoch_id": epoch_id,
                }
            )

    contract_snapshot = [
        sorted(observable),
        contract.covered_range_start.isoformat() if contract.covered_range_start else None,
        contract.covered_range_end.isoformat() if contract.covered_range_end else None,
        contract.gap_semantics,
        timezone_name,
        contract.updated_at.isoformat(),
    ]
    fingerprint = fingerprint_value([fingerprint_parts, contract_snapshot])
    return ComputeResult(input_fingerprint=fingerprint, rows=rows)
