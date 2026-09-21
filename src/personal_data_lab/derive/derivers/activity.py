"""The two deliberately boring Phase 0 derivers: per-source daily activity, and its trailing
7-day sum. Real (Phase 1's coverage work needs exactly this data), but simple enough that any
failure here is a machinery bug, not a statistics bug.

Every date comparison below uses `({date_expr} AT TIME ZONE 'UTC')` rather than
`date_trunc('day', {date_expr})`, specifically so "UTC calendar day" (the provisional
convention these two tables document -- see migration 010) does not silently depend on
whatever timezone the Postgres session happens to be in. `timestamptz AT TIME ZONE 'UTC'`
converts to the UTC wall-clock time as a plain timestamp; comparing that to a date/timestamp
bind parameter is then unambiguous, with no further conversion involved.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ...fingerprints import fingerprint_value
from ..changefeed import WATCHED_KINDS
from ..partitions import parse_local_key
from ..policy import resolve_source_policy
from ..registry import ComputeResult, deriver

# (kind, canonical_table, date_expr), reusing changefeed's watch list so the two never drift
# apart -- if a kind is watched for dirtying, it's counted here too.
_ACTIVITY_KINDS: tuple[tuple[str, str, str], ...] = tuple(
    (label, canon_table, date_expr) for label, _obs, _id, canon_table, date_expr in WATCHED_KINDS
)


def _local_rows_fingerprint(
    connection: Connection, source: str, start: date | None, end: date | None
) -> bytes:
    """Hash over (id, canonical_revision, lifecycle_state) of every watched canonical row for
    this source and month, ordered by id -- any row-level change (even one invisible in the
    aggregated counts, like a revision bump with an unchanged count) changes this fingerprint.
    start=None means the undated bucket: rows with no usable date at all.
    """
    parts: list[list] = []
    for kind, table, date_expr in _ACTIVITY_KINDS:
        if start is None:
            clause, params = f"{date_expr} IS NULL", {"source": source}
        else:
            clause = f"({date_expr} AT TIME ZONE 'UTC') >= :start AND ({date_expr} AT TIME ZONE 'UTC') < :end"
            params = {"source": source, "start": start, "end": end}
        rows = connection.execute(
            text(
                f"SELECT id, canonical_revision, lifecycle_state FROM {table} "
                f"WHERE source = :source AND {clause} ORDER BY id"
            ),
            params,
        ).all()
        parts.append([kind, [[r.id, r.canonical_revision, r.lifecycle_state] for r in rows]])
    return fingerprint_value(parts)


def _clear_month(table: str, connection: Connection, partition_key: str) -> None:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        return  # nothing keyed by day exists for the undated bucket; see module docstring
    connection.execute(
        text(f"DELETE FROM {table} WHERE source = :source AND day >= :start AND day < :end"),
        {"source": source, "start": start, "end": end},
    )


def _clear_daily(connection: Connection, partition_key: str) -> None:
    _clear_month("measure.source_activity_daily", connection, partition_key)


def _clear_7d(connection: Connection, partition_key: str) -> None:
    _clear_month("measure.source_activity_7d", connection, partition_key)


@deriver(
    name="source_activity_daily",
    partition_class="LOCAL",
    final_inference="COMPUTED",
    description=(
        "Per-source, per-day counts of active/tombstoned canonical objects and their version "
        "counts."
    ),
    clear_partition=_clear_daily,
)
def source_activity_daily(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    source, start, end = parse_local_key(partition_key)
    fingerprint = _local_rows_fingerprint(connection, source, start, end)
    if start is None:
        # measure.source_activity_daily.day is NOT NULL, so an undated row has nowhere to land
        # until Phase 1's time normalization gives it a fallback date. Still fingerprinted
        # above, so a real change to undated rows still dirties and reruns this partition.
        return ComputeResult(input_fingerprint=fingerprint, rows=[])

    sensitivity, egress = resolve_source_policy(connection, source)
    rows: list[dict] = []
    for kind, table, date_expr in _ACTIVITY_KINDS:
        result = connection.execute(
            text(
                f"""
                SELECT ({date_expr} AT TIME ZONE 'UTC')::date AS day,
                       COUNT(*) FILTER (WHERE lifecycle_state = 'active') AS active_count,
                       COUNT(*) FILTER (WHERE lifecycle_state = 'provider_deleted') AS tombstoned_count,
                       COALESCE(SUM(canonical_revision), 0) AS versions
                FROM {table}
                WHERE source = :source
                  AND ({date_expr} AT TIME ZONE 'UTC') >= :start
                  AND ({date_expr} AT TIME ZONE 'UTC') < :end
                GROUP BY 1
                """
            ),
            {"source": source, "start": start, "end": end},
        ).all()
        for row in result:
            rows.append(
                {
                    "source": source,
                    "day": row.day,
                    "object_kind": kind,
                    "active_count": int(row.active_count),
                    "tombstoned_count": int(row.tombstoned_count),
                    "versions": int(row.versions),
                    "sensitivity": sensitivity,
                    "egress": egress,
                    "computed_epoch_id": epoch_id,
                }
            )
    return ComputeResult(input_fingerprint=fingerprint, rows=rows)


@deriver(
    name="source_activity_7d",
    partition_class="HALO",
    lookback_halo="6 days",
    inputs=("source_activity_daily",),
    final_inference="COMPUTED",
    description=(
        "Trailing 7-day sums of source_activity_daily, exercising halo propagation across "
        "month boundaries."
    ),
    clear_partition=_clear_7d,
)
def source_activity_7d(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        # Mirrors source_activity_daily's undated handling -- the upstream deriver already
        # tracks the undated bucket's own fingerprint, so there is nothing new to track here.
        return ComputeResult(input_fingerprint=fingerprint_value(["undated"]), rows=[])

    window_start = start - timedelta(days=6)
    daily_rows = connection.execute(
        text(
            """
            SELECT day, object_kind, active_count, tombstoned_count, versions
            FROM measure.source_activity_daily
            WHERE source = :source AND day >= :window_start AND day < :end
            ORDER BY day, object_kind
            """
        ),
        {"source": source, "window_start": window_start, "end": end},
    ).all()
    fingerprint = fingerprint_value(
        [
            [row.day.isoformat(), row.object_kind, row.active_count, row.tombstoned_count, row.versions]
            for row in daily_rows
        ]
    )

    by_kind_day: dict[str, dict[date, tuple[int, int, int]]] = {}
    kinds_seen: set[str] = set()
    for row in daily_rows:
        by_kind_day.setdefault(row.object_kind, {})[row.day] = (
            row.active_count,
            row.tombstoned_count,
            row.versions,
        )
        kinds_seen.add(row.object_kind)

    sensitivity, egress = resolve_source_policy(connection, source)
    rows: list[dict] = []
    cursor = start
    while cursor < end:
        window_days = [cursor - timedelta(days=i) for i in range(7)]
        for kind in kinds_seen:
            per_day = by_kind_day.get(kind, {})
            active = tombstoned = versions = 0
            for day in window_days:
                a, t, v = per_day.get(day, (0, 0, 0))
                active += a
                tombstoned += t
                versions += v
            rows.append(
                {
                    "source": source,
                    "day": cursor,
                    "object_kind": kind,
                    "active_count": active,
                    "tombstoned_count": tombstoned,
                    "versions": versions,
                    "sensitivity": sensitivity,
                    "egress": egress,
                    "computed_epoch_id": epoch_id,
                }
            )
        cursor += timedelta(days=1)
    return ComputeResult(input_fingerprint=fingerprint, rows=rows)
