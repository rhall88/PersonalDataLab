"""Claims and executes dirty derivation partitions.

Known Phase 0 limitation: if a deriver's compute() raises after clear_partition() has already
deleted a partition's prior rows but before all replacement rows are written, the partition
ends up empty (or partial) while correctly marked 'failed' in derive.partition_state. Nothing
in Phase 0 queries measure.* without checking partition_state, so this is a real but currently
harmless gap -- a fast-follow (e.g. a savepoint per partition) once a query layer reads these
tables directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .registry import REGISTRY, DeriverRegistry


@dataclass(frozen=True)
class WorkerOutcome:
    deriver_name: str
    partition_key: str
    status: str  # succeeded | skipped_unchanged | failed
    rows_written: int | None = None
    error: str | None = None


def run_pending(
    engine: Engine,
    *,
    registry: DeriverRegistry = REGISTRY,
    include_global: bool = False,
    limit: int | None = None,
) -> list[WorkerOutcome]:
    """Claim and process dirty partitions one at a time, each in its own transaction.

    One transaction per partition (rather than one long transaction for the whole run) is what
    makes `FOR UPDATE ... SKIP LOCKED` actually let multiple workers make concurrent progress --
    each claim's lock is held only as long as that one partition takes to compute, and a
    failure on one partition can never roll back another partition's already-committed result.

    A real finding from Phase 5's implementation: every partition `mark_dirty_and_propagate`
    dirties within one `sync()` transaction shares the exact same `dirty_since` (Postgres's
    `CURRENT_TIMESTAMP` is constant for a whole transaction), so `ORDER BY dirty_since` alone
    gives no deterministic order among a LOCAL deriver and the HALO/GLOBAL downstream it just
    dirtied together -- a downstream partition could be claimed and computed before its own
    upstream partition in the same batch, silently producing an empty or stale result that
    nothing re-triggers within that batch. `mark_dirty_and_propagate`'s recursion always inserts
    a deriver's own partition rows before recursing into its downstream, so for the common case
    (partitions freshly dirtied together, not merely re-dirtied) physical insertion order tracks
    topological order -- `ps.ctid` is added as a secondary sort key to make the claim order
    respect that, rather than leaving it to whatever order Postgres's query plan happens to
    return ties in. This is a tiebreak, not a scheduler redesign: it does not guarantee
    topological order for every scenario (e.g. partitions re-dirtied by an UPDATE, whose ctid
    reflects the new physical tuple location, not original insertion order), so a deriver that
    truly cannot tolerate stale sibling state should still recompute its own inputs inline, the
    same precedent signals.py and interaction.py already established for GLOBAL siblings.
    """
    outcomes: list[WorkerOutcome] = []
    while limit is None or len(outcomes) < limit:
        with engine.begin() as connection:
            claimed = _claim_one(connection, include_global=include_global)
            if claimed is None:
                break
            deriver_name, partition_key, deriver_version_id = claimed
            deriver = registry.get(deriver_name)
            outcome = _process(connection, deriver, deriver_version_id, partition_key)
        outcomes.append(outcome)
    return outcomes


def _claim_one(connection, *, include_global: bool):
    partition_filter = "" if include_global else "AND d.partition_class <> 'GLOBAL'"
    row = connection.execute(
        text(
            f"""
            SELECT ps.deriver_version_id, ps.partition_key, dv.deriver_name
            FROM derive.partition_state ps
            JOIN derive.deriver_version dv ON dv.id = ps.deriver_version_id AND dv.status = 'active'
            JOIN derive.deriver d ON d.name = dv.deriver_name
            WHERE ps.state = 'dirty' {partition_filter}
            ORDER BY ps.dirty_since NULLS LAST, ps.ctid
            FOR UPDATE OF ps SKIP LOCKED
            LIMIT 1
            """
        )
    ).first()
    if row is None:
        return None
    connection.execute(
        text(
            "UPDATE derive.partition_state SET state = 'running' "
            "WHERE deriver_version_id = :vid AND partition_key = :key"
        ),
        {"vid": row.deriver_version_id, "key": row.partition_key},
    )
    return row.deriver_name, row.partition_key, row.deriver_version_id


def _process(connection, deriver, deriver_version_id: int, partition_key: str) -> WorkerOutcome:
    started_at = datetime.now(UTC)
    epoch_id = connection.execute(text("SELECT MAX(id) FROM derive.evidence_epoch")).scalar_one()
    try:
        previous_fingerprint = connection.execute(
            text(
                "SELECT input_fingerprint FROM derive.partition_state "
                "WHERE deriver_version_id = :vid AND partition_key = :key"
            ),
            {"vid": deriver_version_id, "key": partition_key},
        ).scalar_one()

        result = deriver.compute(connection, partition_key, epoch_id)

        if previous_fingerprint is not None and bytes(previous_fingerprint) == result.input_fingerprint:
            status, rows_written = "skipped_unchanged", None
        else:
            deriver.clear_partition(connection, partition_key)
            rows_written = _write_rows(connection, deriver.output_relation, result.rows)
            status = "succeeded"

        connection.execute(
            text(
                """
                UPDATE derive.partition_state
                SET state = 'clean', input_fingerprint = :fp, computed_at = CURRENT_TIMESTAMP,
                    computed_epoch_id = :epoch, attempts = attempts + 1, last_error = NULL
                WHERE deriver_version_id = :vid AND partition_key = :key
                """
            ),
            {
                "fp": result.input_fingerprint,
                "epoch": epoch_id,
                "vid": deriver_version_id,
                "key": partition_key,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO derive.derivation_run
                    (deriver_version_id, partition_key, epoch_id, input_fingerprint, status,
                     rows_written, started_at, finished_at)
                VALUES (:vid, :key, :epoch, :fp, :status, :rows_written, :started_at,
                        CURRENT_TIMESTAMP)
                """
            ),
            {
                "vid": deriver_version_id,
                "key": partition_key,
                "epoch": epoch_id,
                "fp": result.input_fingerprint,
                "status": status,
                "rows_written": rows_written,
                "started_at": started_at,
            },
        )
        return WorkerOutcome(deriver.name, partition_key, status, rows_written)

    except Exception as error:  # noqa: BLE001 -- deliberately broad: any deriver failure must
        # be recorded as 'failed', never propagate and abort the whole run. Caught here (still
        # inside the caller's transaction) rather than after it, so the failure record below
        # commits along with whatever partial writes clear_partition/compute already made --
        # see the module docstring note on partial-write visibility.
        connection.execute(
            text(
                """
                UPDATE derive.partition_state
                SET state = 'failed', attempts = attempts + 1, last_error = :err
                WHERE deriver_version_id = :vid AND partition_key = :key
                """
            ),
            {"err": str(error), "vid": deriver_version_id, "key": partition_key},
        )
        connection.execute(
            text(
                """
                INSERT INTO derive.derivation_run
                    (deriver_version_id, partition_key, epoch_id, status, started_at, finished_at)
                VALUES (:vid, :key, :epoch, 'failed', :started_at, CURRENT_TIMESTAMP)
                """
            ),
            {
                "vid": deriver_version_id,
                "key": partition_key,
                "epoch": epoch_id,
                "started_at": started_at,
            },
        )
        return WorkerOutcome(deriver.name, partition_key, "failed", error=str(error))


def _write_rows(connection, output_relation: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    columns = list(rows[0].keys())
    for row in rows:
        if list(row.keys()) != columns:
            raise ValueError(
                f"{output_relation}: every row from one compute() call must share the same "
                f"column set"
            )
    column_list = ", ".join(columns)
    placeholders = ", ".join(f":{c}" for c in columns)
    connection.execute(
        text(f"INSERT INTO {output_relation} ({column_list}) VALUES ({placeholders})"),
        rows,
    )
    return len(rows)
