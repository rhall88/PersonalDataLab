from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .partitions import month_key, undated_key

_DIRTYING_OUTCOMES = ("inserted", "updated", "conflicted", "tombstoned")

# (label, observations_table, id_column, canonical_table, date_expr). relationships are
# deliberately excluded: they represent structural edges between canonical objects and have no
# occurred_at-equivalent column, so there is no day to bucket them by.
WATCHED_KINDS: tuple[tuple[str, str, str, str, str], ...] = (
    ("conversation", "conversation_observations", "conversation_id", "conversations", "started_at"),
    ("event", "event_observations", "event_id", "events", "occurred_at"),
    ("record", "record_observations", "record_id", "records", "occurred_at"),
    ("asset", "asset_observations", "asset_id", "assets", "COALESCE(captured_at, created_at)"),
)


@dataclass(frozen=True)
class SyncResult:
    epoch_id: int | None
    dirtied_local_keys: frozenset[str]

    @property
    def is_noop(self) -> bool:
        return self.epoch_id is None


def _outcome_placeholders() -> str:
    return ", ".join(f"'{o}'" for o in _DIRTYING_OUTCOMES)


def read_dirty_local_keys(connection: Connection, *, since_import_run_id: int) -> tuple[frozenset[str], int | None]:
    """Read every *_observations row past `since_import_run_id` with a dirtying outcome, and
    return the set of LOCAL partition keys it touches plus the highest import_run_id seen.

    Only reads observations, never *_versions or the canonical tables' own audit columns --
    this is exactly the "zero importer changes" mechanism the Phase 0 plan describes: every
    importer already writes one *_observations row per object per run with an outcome, so the
    change feed is a plain read of data that already exists.
    """
    dirty: set[str] = set()
    max_run_id: int | None = None
    for _label, obs_table, id_col, canon_table, date_expr in WATCHED_KINDS:
        rows = connection.execute(
            text(
                f"""
                SELECT o.import_run_id, c.source, {date_expr} AS activity_at
                FROM {obs_table} o
                JOIN {canon_table} c ON c.id = o.{id_col}
                WHERE o.import_run_id > :since
                  AND o.outcome IN ({_outcome_placeholders()})
                """
            ),
            {"since": since_import_run_id},
        ).all()
        for row in rows:
            max_run_id = row.import_run_id if max_run_id is None else max(max_run_id, row.import_run_id)
            if row.activity_at is None:
                dirty.add(undated_key(row.source))
            else:
                dirty.add(month_key(row.source, row.activity_at.date()))
    return frozenset(dirty), max_run_id


def cursor_value(connection: Connection, *, cursor_name: str = "default") -> int:
    return connection.execute(
        text("SELECT last_import_run_id FROM derive.change_cursor WHERE name = :name"),
        {"name": cursor_name},
    ).scalar_one()


def advance_cursor(connection: Connection, *, max_run_id: int, cursor_name: str = "default") -> int:
    """Move the named cursor forward and close a new evidence epoch recording it, returning the
    epoch's id. Never called with nothing new to record -- callers check is_noop first.
    """
    connection.execute(
        text("UPDATE derive.change_cursor SET last_import_run_id = :v WHERE name = :name"),
        {"v": max_run_id, "name": cursor_name},
    )
    return connection.execute(
        text(
            "INSERT INTO derive.evidence_epoch (max_import_run_id) VALUES (:v) RETURNING id"
        ),
        {"v": max_run_id},
    ).scalar_one()
