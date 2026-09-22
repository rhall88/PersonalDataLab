"""As-of reconstruction (PHASE1_IMPLEMENTATION_PLAN.md): what a canonical object looked like as
of a historical import_runs.id cutoff, using columns Phase 0 already wrote on every import --
no canonical schema change, no new ingestion-path behavior.

An object is visible as of cutoff N iff first_observed_run_id <= N and it was not tombstoned at
or before N (tombstone_run_id IS NULL OR tombstone_run_id > N). Its content as of N is the
*_versions row with the greatest first_observed_run_id <= N, joined per-row via LATERAL so one
query pattern covers all five object kinds instead of five hand-written joins.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

# (canonical_table, versions_table, id_column). Allow-listed rather than accepting an arbitrary
# table name from a caller, since these five names are interpolated directly into SQL text.
_OBJECT_KINDS: dict[str, tuple[str, str, str]] = {
    "conversations": ("conversations", "conversation_versions", "conversation_id"),
    "events": ("events", "event_versions", "event_id"),
    "records": ("records", "record_versions", "record_id"),
    "assets": ("assets", "asset_versions", "asset_id"),
    "relationships": ("relationships", "relationship_versions", "relationship_id"),
}


def _as_of(connection: Connection, object_kind: str, cutoff_run_id: int) -> list[dict]:
    table, versions_table, id_col = _OBJECT_KINDS[object_kind]
    rows = connection.execute(
        text(
            f"""
            SELECT c.*, v.content_fingerprint AS version_content_fingerprint,
                   v.fingerprint_version AS version_fingerprint_version,
                   v.normalized_payload AS version_normalized_payload,
                   v.payload_fidelity AS version_payload_fidelity,
                   v.first_observed_run_id AS version_first_observed_run_id
            FROM {table} c
            LEFT JOIN LATERAL (
                SELECT *
                FROM {versions_table}
                WHERE {id_col} = c.id AND first_observed_run_id <= :cutoff
                ORDER BY first_observed_run_id DESC
                LIMIT 1
            ) v ON TRUE
            WHERE c.first_observed_run_id <= :cutoff
              AND (c.tombstone_run_id IS NULL OR c.tombstone_run_id > :cutoff)
            ORDER BY c.id
            """
        ),
        {"cutoff": cutoff_run_id},
    ).mappings().all()
    return [dict(row) for row in rows]


def as_of_conversations(connection: Connection, cutoff_run_id: int) -> list[dict]:
    return _as_of(connection, "conversations", cutoff_run_id)


def as_of_events(connection: Connection, cutoff_run_id: int) -> list[dict]:
    return _as_of(connection, "events", cutoff_run_id)


def as_of_records(connection: Connection, cutoff_run_id: int) -> list[dict]:
    return _as_of(connection, "records", cutoff_run_id)


def as_of_assets(connection: Connection, cutoff_run_id: int) -> list[dict]:
    return _as_of(connection, "assets", cutoff_run_id)


def as_of_relationships(connection: Connection, cutoff_run_id: int) -> list[dict]:
    return _as_of(connection, "relationships", cutoff_run_id)


AS_OF_FUNCTIONS: dict[str, Callable[[Connection, int], list[dict]]] = {
    "conversations": as_of_conversations,
    "events": as_of_events,
    "records": as_of_records,
    "assets": as_of_assets,
    "relationships": as_of_relationships,
}


def resolve_cutoff_run_id(
    connection: Connection, *, cutoff_run_id: int | None, cutoff_at: datetime | None
) -> int:
    """Resolve a --cutoff-run/--cutoff-at pair (exactly one given) into a concrete
    import_runs.id. --cutoff-at uses started_at, not completed_at: completed_at is NULL for a
    run that never finished, and started_at is what pins a run to the append-only
    import_runs.id order the rest of Phase 1 relies on.
    """
    if (cutoff_run_id is None) == (cutoff_at is None):
        raise ValueError("exactly one of cutoff_run_id or cutoff_at must be given")
    if cutoff_run_id is not None:
        return cutoff_run_id
    row = connection.execute(
        text("SELECT MAX(id) AS id FROM import_runs WHERE started_at <= :cutoff_at"),
        {"cutoff_at": cutoff_at},
    ).first()
    if row is None or row.id is None:
        raise ValueError(f"no import run at or before {cutoff_at.isoformat()}")
    return row.id
