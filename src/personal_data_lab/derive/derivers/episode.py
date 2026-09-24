"""Phase 4's episodes (PHASE4_IMPLEMENTATION_PLAN.md decisions 6-8): declared (a person names a
period), detected (cross-source-family change-point clustering), and -- explicitly out of scope
for this phase -- derived (trips, project spans, jobs; no location/git/payroll source exists in
this corpus, the same finding Phase 3 made for its own scope).

`declare_episode`/`list_episodes` are plain functions the CLI calls directly, not derivers --
a person naming a period isn't a derivation. `episode_detect_candidates` is a GLOBAL deriver whose
clear_partition only removes kind='detected' AND status='candidate' rows, so a declared episode
(or a detected one a person has since confirmed or rejected) is never touched by re-detection.

Reuses signals.py's _all_known_series/_change_points rather than reading signal.change_point, for
the same cross-GLOBAL-deriver-ordering reason documented in signals.py's own module docstring.
"""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ...fingerprints import fingerprint_value
from ..registry import ComputeResult, deriver
from .signals import _all_known_series, _change_points

# Change points from >= 2 different source families within this many days of each other form one
# detected candidate episode (decision 7). Chosen to be wide enough to catch a real transition
# that shows up in different sources a few weeks apart (evidence rarely lands on the same day
# across independent provenance families), narrow enough that it doesn't chain unrelated
# transitions months apart into one meaningless cluster.
_CLUSTER_WINDOW_DAYS = 21


def declare_episode(engine: Engine, *, start: date, end: date, label: str, note: str | None = None) -> int:
    if end < start:
        raise ValueError(f"end ({end}) is before start ({start})")
    with engine.begin() as connection:
        return connection.execute(
            text(
                """
                INSERT INTO episode.episode
                    (kind, status, start_range_low, start_range_high, end_range_low,
                     end_range_high, label, note, evidence)
                VALUES ('declared', 'confirmed', :start, :start, :end, :end, :label, :note, '{}'::jsonb)
                RETURNING id
                """
            ),
            {"start": start, "end": end, "label": label, "note": note},
        ).scalar_one()


_EPISODE_UPDATE_COLUMNS = (
    "id, kind, status, start_range_low, start_range_high, end_range_low, end_range_high, "
    "label, note, evidence, created_at"
)


def _resolve_candidate(connection: Connection, episode_id: int):
    """Shared guard for confirm_episode/reject_episode: only a kind='detected',
    status='candidate' row can transition. A declared episode is already 'confirmed' at
    creation (see declare_episode above), and re-deciding an already-decided detected row would
    silently discard the record of who decided what -- the same one-way-decision precedent
    identity merge/split already established for identity_links.
    """
    row = connection.execute(
        text("SELECT kind, status FROM episode.episode WHERE id = :id FOR UPDATE"),
        {"id": episode_id},
    ).first()
    if row is None:
        raise ValueError(f"no episode with id {episode_id}")
    if row.kind != "detected":
        raise ValueError(f"episode {episode_id} is kind={row.kind!r}, not 'detected'")
    if row.status != "candidate":
        raise ValueError(f"episode {episode_id} is already status={row.status!r}")


def confirm_episode(
    engine: Engine, episode_id: int, *, label: str | None = None, note: str | None = None
) -> dict:
    """Confirm a detected candidate episode in place: status -> 'confirmed', optionally
    attaching a human label/note naming what the clustered change points actually were.
    `episode_detect_candidates`'s clear_partition only ever removes kind='detected' AND
    status='candidate' rows, so a confirmed row is never touched by later re-detection --
    it becomes permanent, inspectable evidence of a human decision, the same as a declared
    episode.
    """
    with engine.begin() as connection:
        _resolve_candidate(connection, episode_id)
        updated = connection.execute(
            text(
                f"""
                UPDATE episode.episode
                SET status = 'confirmed', label = COALESCE(:label, label),
                    note = COALESCE(:note, note)
                WHERE id = :id
                RETURNING {_EPISODE_UPDATE_COLUMNS}
                """
            ),
            {"id": episode_id, "label": label, "note": note},
        ).mappings().one()
        return dict(updated)


def reject_episode(engine: Engine, episode_id: int, *, note: str | None = None) -> dict:
    """Reject a detected candidate episode in place: status -> 'rejected', permanently --
    same non-regeneration guarantee as confirm_episode. Rejecting records "these change points
    don't correspond to anything real" as durably as confirming records the opposite, rather
    than leaving the candidate to resurface indefinitely on every future `derive run`.
    """
    with engine.begin() as connection:
        _resolve_candidate(connection, episode_id)
        updated = connection.execute(
            text(
                f"""
                UPDATE episode.episode
                SET status = 'rejected', note = COALESCE(:note, note)
                WHERE id = :id
                RETURNING {_EPISODE_UPDATE_COLUMNS}
                """
            ),
            {"id": episode_id, "note": note},
        ).mappings().one()
        return dict(updated)


def list_episodes(engine: Engine, *, kind: str | None = None) -> list[dict]:
    query = (
        "SELECT id, kind, status, start_range_low, start_range_high, end_range_low, "
        "end_range_high, label, note, evidence, created_at FROM episode.episode"
    )
    params: dict = {}
    if kind is not None:
        query += " WHERE kind = :kind"
        params["kind"] = kind
    query += " ORDER BY start_range_low"
    with engine.connect() as connection:
        rows = connection.execute(text(query), params).mappings().all()
    return [dict(row) for row in rows]


def _metric_source_families(connection: Connection) -> dict[int, str]:
    """A metric's source family, when it has exactly one -- a per-source metric from Phase 3's
    METRIC_CATALOG. Composite metrics (messaging.total_messages_count and friends) depend on
    multiple sources and are excluded here: "independent metrics" (decision 7) means
    single-source metrics from genuinely independent provenance families, not a composite that
    already mixes several.
    """
    rows = connection.execute(text("SELECT metric_id, source FROM derive.metric_dependency")).all()
    by_metric: dict[int, set[str]] = {}
    for row in rows:
        by_metric.setdefault(row.metric_id, set()).add(row.source)
    return {metric_id: next(iter(sources)) for metric_id, sources in by_metric.items() if len(sources) == 1}


def _cluster_change_points(points: list[tuple[date, int, str]]) -> list[list[tuple[date, int, str]]]:
    points = sorted(points, key=lambda p: p[0])
    clusters: list[list[tuple[date, int, str]]] = []
    current: list[tuple[date, int, str]] = []
    for point in points:
        if current and (point[0] - current[-1][0]).days > _CLUSTER_WINDOW_DAYS:
            clusters.append(current)
            current = []
        current.append(point)
    if current:
        clusters.append(current)
    return clusters


def detect_episode_candidates(connection: Connection) -> list[dict]:
    source_family = _metric_source_families(connection)
    metric_names = dict(connection.execute(text("SELECT id, name FROM derive.metric_def")).all())

    points: list[tuple[date, int, str]] = []
    for series in _all_known_series(connection):
        source = source_family.get(series.metric_id)
        if source is None:
            continue
        for cp_date in _change_points(series):
            points.append((cp_date, series.metric_id, source))

    candidates: list[dict] = []
    for cluster in _cluster_change_points(points):
        families = {p[2] for p in cluster}
        if len(families) < 2:
            continue
        dates = [p[0] for p in cluster]
        evidence = {
            "change_points": [
                {"metric": metric_names[metric_id], "source": source, "date": cp_date.isoformat()}
                for cp_date, metric_id, source in cluster
            ]
        }
        candidates.append(
            {
                "kind": "detected",
                "status": "candidate",
                "start_range_low": min(dates),
                "start_range_high": max(dates),
                "end_range_low": min(dates),
                "end_range_high": max(dates),
                "label": None,
                "note": None,
                "evidence": json.dumps(evidence),
            }
        )
    return candidates


def _clear_detected_candidates(connection: Connection, partition_key: str) -> None:
    connection.execute(
        text("DELETE FROM episode.episode WHERE kind = 'detected' AND status = 'candidate'")
    )


@deriver(
    name="episode_detect_candidates",
    partition_class="GLOBAL",
    final_inference="COMPUTED",
    description=(
        "Cross-source-family change-point clustering into candidate detected episodes "
        "(PHASE4_IMPLEMENTATION_PLAN.md decision 7)."
    ),
    output_relation="episode.episode",
    inputs=("measure_metric_daily", "measure_metric_daily_global"),
    clear_partition=_clear_detected_candidates,
)
def episode_detect_candidates(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    candidates = detect_episode_candidates(connection)
    fingerprint = fingerprint_value(
        [[c["start_range_low"].isoformat(), c["end_range_high"].isoformat(), c["evidence"]] for c in candidates]
    )
    return ComputeResult(input_fingerprint=fingerprint, rows=candidates)


__all__ = [
    "declare_episode",
    "detect_episode_candidates",
    "episode_detect_candidates",
    "list_episodes",
]
