from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .partitions import GLOBAL_PARTITION_KEY, halo_months, interval_to_days
from .versioning import active_version_id


def mark_dirty(connection: Connection, deriver_name: str, partition_keys: Iterable[str]) -> None:
    """Mark the given partitions dirty for a deriver's active version.

    A partition currently 'running' is left alone -- its own completion will re-fingerprint
    against current inputs anyway, and flipping it to dirty mid-run would just cause a
    redundant immediate rerun. dirty_since is preserved across repeated dirtying (COALESCE)
    so a partition that keeps getting touched doesn't perpetually look "just now" dirtied.
    """
    version_id = active_version_id(connection, deriver_name)
    for key in partition_keys:
        connection.execute(
            text(
                """
                INSERT INTO derive.partition_state (deriver_version_id, partition_key, state, dirty_since)
                VALUES (:vid, :key, 'dirty', CURRENT_TIMESTAMP)
                ON CONFLICT (deriver_version_id, partition_key) DO UPDATE SET
                    state = 'dirty',
                    dirty_since = COALESCE(
                        derive.partition_state.dirty_since, EXCLUDED.dirty_since
                    )
                WHERE derive.partition_state.state <> 'running'
                """
            ),
            {"vid": version_id, "key": key},
        )


def mark_dirty_and_propagate(connection: Connection, deriver_name: str, keys: frozenset[str]) -> None:
    """Mark `deriver_name`'s partitions dirty for `keys`, then recursively dirty every
    downstream deriver's corresponding partitions, expanding month keys through each
    downstream deriver's own halo. GLOBAL downstream derivers are marked dirty too (a single
    row keyed by GLOBAL_PARTITION_KEY) but are never run implicitly -- only `derive run
    --global` processes them, per the Phase 0 plan.
    """
    if not keys:
        return
    mark_dirty(connection, deriver_name, keys)
    for downstream_name in _downstream_names(connection, deriver_name):
        downstream = _deriver_row(connection, downstream_name)
        if downstream.partition_class == "GLOBAL":
            expanded = frozenset({GLOBAL_PARTITION_KEY})
        elif downstream.partition_class == "LOCAL":
            expanded = keys
        else:
            lookback_days = interval_to_days(downstream.lookback_halo)
            lookahead_days = interval_to_days(downstream.lookahead_halo)
            expanded = frozenset(
                month
                for key in keys
                for month in halo_months(key, lookback_days, lookahead_days)
            )
        mark_dirty_and_propagate(connection, downstream_name, expanded)


def _downstream_names(connection: Connection, deriver_name: str) -> tuple[str, ...]:
    return tuple(
        connection.execute(
            text("SELECT downstream FROM derive.deriver_dependency WHERE upstream = :u"),
            {"u": deriver_name},
        ).scalars()
    )


def _deriver_row(connection: Connection, name: str):
    return connection.execute(
        text(
            "SELECT partition_class, lookback_halo, lookahead_halo FROM derive.deriver "
            "WHERE name = :name"
        ),
        {"name": name},
    ).one()
