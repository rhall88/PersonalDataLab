from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .partitions import GLOBAL_PARTITION_KEY, halo_months, interval_to_days, month_key, undated_key
from .registry import Deriver
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
        expanded = _expand_for_downstream(
            downstream.partition_class, downstream.lookback_halo, downstream.lookahead_halo, keys
        )
        mark_dirty_and_propagate(connection, downstream_name, expanded)


def _expand_for_downstream(
    partition_class: str, lookback_halo, lookahead_halo, keys: frozenset[str]
) -> frozenset[str]:
    """Project an upstream LOCAL key set onto a downstream deriver's own partition space,
    per that downstream deriver's partition_class. Shared by `mark_dirty_and_propagate` (an
    upstream change propagating forward) and `backfill_new_derivers` (a newly-added deriver
    adopting whatever partition space its upstream already has) -- both are the same
    "upstream keys -> this deriver's keys" projection, just triggered by different events.
    """
    if partition_class == "GLOBAL":
        return frozenset({GLOBAL_PARTITION_KEY})
    if partition_class == "LOCAL":
        return keys
    lookback_days = interval_to_days(lookback_halo)
    lookahead_days = interval_to_days(lookahead_halo)
    return frozenset(
        month for key in keys for month in halo_months(key, lookback_days, lookahead_days)
    )


def backfill_new_derivers(
    connection: Connection, new_derivers: Iterable[Deriver]
) -> dict[str, int]:
    """Seed initial dirty partitions for derivers whose `derive.deriver_version` row is being
    created for the very first time in this `sync()` call -- i.e. a deriver newly added to the
    registry against a database that already has history.

    Without this, a brand-new deriver gets no retroactive dirtying at all: `sync()` only reacts
    to `*_observations` rows written *after* the change-feed cursor, and `rebuild()` only
    re-dirties `partition_key` values already present in `derive.partition_state` -- neither has
    any way to discover a partition space that has never existed for this deriver before.
    Phases 1, 3 and 4 each hit exactly this gap and were worked around with a one-off script
    (see PROJECT_STATE.md); this makes that workaround permanent and automatic instead of
    something to remember to write again for Phase 5 and beyond.

    Callers must pass only derivers that genuinely had no `derive.deriver_version` row before
    this `sync()` call's own version-ensuring loop ran (see `derive/__init__.py:sync()`) -- not
    merely derivers with zero rows in `derive.partition_state` right now, since a test (or a
    real `derive-rebuild`) can legitimately empty that table for a long-established deriver
    without it being "new".

    A root deriver (no inputs) seeds from `declare.source_contract`'s covered_range per source --
    the same key space `changefeed.py`'s `read_dirty_local_keys` would eventually produce as real
    observations arrive. A non-root deriver seeds from whatever partition keys its upstream
    deriver(s) already have in `derive.partition_state` (any state, not just clean -- a key
    existing at all proves the upstream partition space, which is all this needs), projected
    through this deriver's own partition_class/halo via `_expand_for_downstream`. Each seed then
    reuses `mark_dirty_and_propagate`, so anything downstream of a newly-seeded deriver -- new or
    pre-existing -- is dirtied too in the same pass; a deriver that ends up seeded twice this way
    (its own turn, then again via an upstream new deriver's propagation) is harmless, since
    marking an already-dirty partition dirty again is a no-op.
    """
    seeded: dict[str, int] = {}
    for deriver_obj in new_derivers:
        if deriver_obj.inputs:
            keys = _upstream_keys_projected(connection, deriver_obj)
        else:
            keys = _root_keys_from_contracts(connection, deriver_obj.partition_class)
        if keys:
            mark_dirty_and_propagate(connection, deriver_obj.name, keys)
        seeded[deriver_obj.name] = len(keys)
    return seeded


def _upstream_keys_projected(connection: Connection, deriver_obj: Deriver) -> frozenset[str]:
    upstream_keys: set[str] = set()
    for upstream_name in deriver_obj.inputs:
        upstream_version_id = active_version_id(connection, upstream_name)
        upstream_keys.update(
            connection.execute(
                text(
                    "SELECT DISTINCT partition_key FROM derive.partition_state "
                    "WHERE deriver_version_id = :vid"
                ),
                {"vid": upstream_version_id},
            ).scalars()
        )
    return _expand_for_downstream(
        deriver_obj.partition_class,
        deriver_obj.lookback_halo,
        deriver_obj.lookahead_halo,
        frozenset(upstream_keys),
    )


def _root_keys_from_contracts(connection: Connection, partition_class: str) -> frozenset[str]:
    if partition_class == "GLOBAL":
        return frozenset({GLOBAL_PARTITION_KEY})
    rows = connection.execute(
        text("SELECT source, covered_range_start, covered_range_end FROM declare.source_contract")
    ).all()
    keys: set[str] = set()
    for row in rows:
        # Every declared source gets its own undated bucket seeded regardless of whether it also
        # has a covered_range -- a real gap Phase 6's media-transcription rollout found: 487 of
        # 494 real audio assets (apple/facebook/google) have neither captured_at nor created_at
        # at all, so they only ever exist in the undated partition, which this function never
        # seeded before. The dated month range below covers whatever DOES have a usable date;
        # this covers what doesn't.
        keys.add(undated_key(row.source))
        if row.covered_range_start is None or row.covered_range_end is None:
            continue
        cursor = date(row.covered_range_start.year, row.covered_range_start.month, 1)
        while cursor <= row.covered_range_end:
            keys.add(month_key(row.source, cursor))
            cursor = (
                date(cursor.year + 1, 1, 1)
                if cursor.month == 12
                else date(cursor.year, cursor.month + 1, 1)
            )
    return frozenset(keys)


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
