"""Phase 0 of the derived meaning architecture: derivation machinery that lets derived data
exist without ever touching canonical evidence. See DERIVED_MEANING_ARCHITECTURE.md and
PHASE0_IMPLEMENTATION_PLAN.md at the repo root.

This module is the public entry point the CLI uses (sync/run/status/rebuild/promote);
registry.py, versioning.py, partitions.py, changefeed.py, scheduler.py, worker.py and policy.py
are the pieces it composes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.engine import Engine

from . import derivers as _derivers  # noqa: F401 -- import side effect populates REGISTRY
from .changefeed import advance_cursor, cursor_value, read_dirty_local_keys
from .derivers.metrics import sync_metric_catalog
from .registry import REGISTRY, DeriverRegistry
from .scheduler import backfill_new_derivers, mark_dirty_and_propagate
from .versioning import VersioningError, ensure_deriver_version
from .versioning import promote as _promote
from .worker import WorkerOutcome, run_pending

__all__ = [
    "SyncOutcome",
    "VersioningError",
    "WorkerOutcome",
    "promote",
    "rebuild",
    "run",
    "status",
    "sync",
]


@dataclass(frozen=True)
class SyncOutcome:
    epoch_id: int | None
    dirtied_local_keys: int
    backfilled_derivers: dict[str, int] = field(default_factory=dict)

    @property
    def is_noop(self) -> bool:
        return self.epoch_id is None


def sync(
    engine: Engine, *, registry: DeriverRegistry = REGISTRY, cursor_name: str = "default"
) -> SyncOutcome:
    """Sync the registry to the database, read the change feed since the cursor, close a new
    evidence epoch if anything is new, and dirty every root deriver's affected partitions
    (propagating downstream through the DAG). A no-op sync (nothing new since the cursor)
    closes no epoch and dirties nothing -- this is exit test 1 from the Phase 0 plan.

    Before any of that, any deriver seeing its first-ever `derive.deriver_version` row (i.e.
    genuinely new to this database, not just freshly cleared) gets backfilled: its initial
    partition space is seeded from whatever history its upstream (or, for a root deriver,
    `declare.source_contract`) already has, so a deriver added after the change-feed cursor has
    already moved past all existing evidence still gets a chance to compute over that evidence.
    See `scheduler.backfill_new_derivers` for why this exists and how it stays safe to call on
    every sync, not just the first one after a new deriver ships.
    """
    with engine.begin() as connection:
        registry.sync(connection)
        sync_metric_catalog(connection)
        known_before = set(
            connection.execute(
                text("SELECT DISTINCT deriver_name FROM derive.deriver_version")
            ).scalars()
        )
        for deriver_obj in registry:
            ensure_deriver_version(connection, deriver_obj)
        new_derivers = [d for d in registry if d.name not in known_before]
        backfilled = backfill_new_derivers(connection, new_derivers)

        since = cursor_value(connection, cursor_name=cursor_name)
        dirty_keys, max_run_id = read_dirty_local_keys(connection, since_import_run_id=since)
        if max_run_id is None:
            return SyncOutcome(epoch_id=None, dirtied_local_keys=0, backfilled_derivers=backfilled)

        epoch_id = advance_cursor(connection, max_run_id=max_run_id, cursor_name=cursor_name)
        roots = [deriver_obj for deriver_obj in registry if not deriver_obj.inputs]
        for root in roots:
            mark_dirty_and_propagate(connection, root.name, dirty_keys)
        return SyncOutcome(
            epoch_id=epoch_id, dirtied_local_keys=len(dirty_keys), backfilled_derivers=backfilled
        )


def run(
    engine: Engine,
    *,
    registry: DeriverRegistry = REGISTRY,
    include_global: bool = False,
    limit: int | None = None,
) -> list[WorkerOutcome]:
    return run_pending(engine, registry=registry, include_global=include_global, limit=limit)


def status(engine: Engine, *, registry: DeriverRegistry = REGISTRY) -> list[dict]:
    report = []
    with engine.connect() as connection:
        for deriver_obj in registry:
            active = connection.execute(
                text(
                    "SELECT version_hash FROM derive.deriver_version "
                    "WHERE deriver_name = :name AND status = 'active'"
                ),
                {"name": deriver_obj.name},
            ).first()
            counts = dict(
                connection.execute(
                    text(
                        """
                        SELECT ps.state, COUNT(*)
                        FROM derive.partition_state ps
                        JOIN derive.deriver_version dv ON dv.id = ps.deriver_version_id
                        WHERE dv.deriver_name = :name
                        GROUP BY ps.state
                        """
                    ),
                    {"name": deriver_obj.name},
                ).all()
            )
            last_epoch = connection.execute(
                text(
                    """
                    SELECT MAX(ps.computed_epoch_id)
                    FROM derive.partition_state ps
                    JOIN derive.deriver_version dv ON dv.id = ps.deriver_version_id
                    WHERE dv.deriver_name = :name
                    """
                ),
                {"name": deriver_obj.name},
            ).scalar_one()
            report.append(
                {
                    "name": deriver_obj.name,
                    "active_version_hash": active.version_hash.hex() if active else None,
                    "partitions": {
                        "dirty": counts.get("dirty", 0),
                        "running": counts.get("running", 0),
                        "clean": counts.get("clean", 0),
                        "failed": counts.get("failed", 0),
                    },
                    "last_computed_epoch_id": last_epoch,
                }
            )
    return report


def rebuild(
    engine: Engine, *, deriver_name: str | None = None, registry: DeriverRegistry = REGISTRY
) -> int:
    """Clear a deriver's (or every deriver's) existing output and mark every already-known
    partition dirty again. Reuses partition_key values already in partition_state rather than
    rediscovering them from the change feed -- a full rebuild replays what's known, it doesn't
    re-derive which partitions exist. `DROP SCHEMA measure CASCADE` followed by this is the
    literal disposability test the Phase 0 plan calls for (architecture inv. 20); this function
    covers the same guarantee without requiring the schema to actually be dropped first.
    """
    names = [deriver_name] if deriver_name else [d.name for d in registry]
    dirtied = 0
    with engine.begin() as connection:
        for name in names:
            deriver_obj = registry.get(name)
            keys = connection.execute(
                text(
                    """
                    SELECT ps.partition_key
                    FROM derive.partition_state ps
                    JOIN derive.deriver_version dv ON dv.id = ps.deriver_version_id
                    WHERE dv.deriver_name = :name AND dv.status = 'active'
                    """
                ),
                {"name": name},
            ).scalars().all()
            for key in keys:
                deriver_obj.clear_partition(connection, key)
            connection.execute(
                text(
                    """
                    UPDATE derive.partition_state AS ps
                    SET state = 'dirty', input_fingerprint = NULL,
                        dirty_since = COALESCE(ps.dirty_since, CURRENT_TIMESTAMP)
                    FROM derive.deriver_version dv
                    WHERE ps.deriver_version_id = dv.id AND dv.deriver_name = :name
                    """
                ),
                {"name": name},
            )
            dirtied += len(keys)
    return dirtied


def promote(engine: Engine, deriver_name: str, version_hash_hex: str) -> None:
    with engine.begin() as connection:
        _promote(connection, deriver_name, version_hash_hex)
