"""Phase 10's event layer (PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md decisions 10-12): the model
never does date math (decision 10) -- `event_resolve_time_expressions` is a small, auditable,
deterministic rule table turning a raw literal expression ("yesterday", "last May") into a real
date range against the text unit's own `started_at` anchor, kept OUT of generative.extraction
(decision 11) since it's derived-by-code, not model output.

`event_resolve_candidates` resolves the one open design question v2 left unsettled (decision 12):
cross-source event stitching is a real, distinct mechanism from Phase 4's coarser episode
detection, implemented here as its own candidate/confirm pair rather than folded into episodes.
Clustering signals: event-type match, resolved-range overlap, resolved-entity overlap (via
entity.resolved_mention), embedding similarity, and -- mirroring episode.py's own precedent for
"cross-source" -- a hard requirement of >=2 distinct source families per cluster.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import date, timedelta

import numpy as np
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ...fingerprints import fingerprint_value
from ..partitions import parse_local_key
from ..registry import ComputeResult, deriver

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_MONTHS = (
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
)

# decision 12's clustering gate: an embedding-similarity threshold has no real rollout data to be
# grounded against yet (Phase 10 hasn't run against the canonical database) -- kept as a named,
# documented default pending real grounding, same precedent clustering.py's own placeholder
# constants set before their real benchmark.
_EMBEDDING_SIMILARITY_THRESHOLD = 0.6


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _week_bounds(d: date) -> tuple[date, date]:
    start = d - timedelta(days=d.weekday())
    return start, start + timedelta(days=7)


def resolve_time_expression(expression: str, anchor: date) -> tuple[tuple[date, date] | None, str]:
    """Pure function: a raw literal expression plus its text unit's own started_at anchor ->
    (resolved_range, resolution_rule). `resolved_range` is a half-open (start, end) tuple or
    None. Never guesses when the pattern doesn't match -- returns (None, 'unparsed').
    """
    normalized = expression.strip().lower()

    if normalized == "today":
        return (anchor, anchor + timedelta(days=1)), "relative_day"
    if normalized == "yesterday":
        return (anchor - timedelta(days=1), anchor), "relative_day"
    if normalized == "tomorrow":
        return (anchor + timedelta(days=1), anchor + timedelta(days=2)), "relative_day"

    match = re.fullmatch(r"(last|next)\s+(\w+)", normalized)
    if match:
        direction, word = match.groups()
        if word in _WEEKDAYS:
            target = _WEEKDAYS.index(word)
            delta = (anchor.weekday() - target) % 7
            if direction == "last":
                day = anchor - timedelta(days=delta or 7)
            else:
                day = anchor + timedelta(days=(7 - delta) % 7 or 7)
            return (day, day + timedelta(days=1)), "relative_weekday"

    if normalized == "last week":
        start, _ = _week_bounds(anchor)
        return (start - timedelta(days=7), start), "relative_week"
    if normalized == "last month":
        this_month_start = date(anchor.year, anchor.month, 1)
        start = _add_months(this_month_start, -1)
        return (start, this_month_start), "relative_month"
    if normalized == "last year":
        return (date(anchor.year - 1, 1, 1), date(anchor.year, 1, 1)), "relative_year"

    match = re.fullmatch(r"(\w+)\s+(\d{4})", normalized)
    if match and match.group(1) in _MONTHS:
        month = _MONTHS.index(match.group(1)) + 1
        year = int(match.group(2))
        start = date(year, month, 1)
        return (start, _add_months(start, 1)), "named_month_year"

    match = re.fullmatch(r"(\d{4})", normalized)
    if match:
        year = int(match.group(1))
        return (date(year, 1, 1), date(year + 1, 1, 1)), "named_year"

    return None, "unparsed"


def _clear_time_resolution(connection: Connection, partition_key: str) -> None:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        return
    connection.execute(
        text(
            """
            DELETE FROM event.time_resolution
            WHERE text_unit_id IN (
                SELECT id FROM semantic.text_unit
                WHERE source = :source AND started_at >= :start AND started_at < :end
            )
            """
        ),
        {"source": source, "end": end, "start": start},
    )


@deriver(
    name="event_resolve_time_expressions",
    partition_class="LOCAL",
    final_inference="COMPUTED",
    description=(
        "Deterministic rule-table resolution of generative.extraction's raw event_time_expression "
        "against each text unit's own started_at anchor -- never a second model call "
        "(PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md decision 11)."
    ),
    output_relation="event.time_resolution",
    inputs=("generative_extract_text_unit",),
    clear_partition=_clear_time_resolution,
)
def event_resolve_time_expressions(
    connection: Connection, partition_key: str, epoch_id: int
) -> ComputeResult:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        return ComputeResult(input_fingerprint=fingerprint_value(["undated"]), rows=[])

    units = connection.execute(
        text(
            """
            SELECT g.text_unit_id, g.event_time_expression, t.started_at
            FROM generative.extraction g
            JOIN semantic.text_unit t ON t.id = g.text_unit_id
            WHERE t.source = :source AND t.started_at >= :start AND t.started_at < :end
            AND g.extraction_status = 'complete'
            AND g.event_time_expression IS NOT NULL AND g.event_time_expression != ''
            ORDER BY g.text_unit_id
            """
        ),
        {"source": source, "start": start, "end": end},
    ).all()

    rows = []
    for text_unit_id, expression, started_at in units:
        resolved_range, rule = resolve_time_expression(expression, started_at.date())
        rows.append(
            {
                "text_unit_id": text_unit_id,
                "raw_expression": expression,
                "resolved_range": (
                    f"[{resolved_range[0].isoformat()},{resolved_range[1].isoformat()})"
                    if resolved_range is not None
                    else None
                ),
                "resolution_rule": rule,
                "computed_epoch_id": epoch_id,
            }
        )
    fingerprint = fingerprint_value(sorted((r["text_unit_id"], r["raw_expression"]) for r in rows))
    return ComputeResult(input_fingerprint=fingerprint, rows=rows)


# --- event_resolve_candidates: cross-source event stitching, candidate-only. -----------------


def _parse_vector(value: str) -> np.ndarray:
    return np.array([float(x) for x in value.strip("[]").split(",")], dtype=np.float64)


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denom) if denom else 0.0


class _UnionFind:
    def __init__(self, items):
        self._parent = {item: item for item in items}

    def find(self, item):
        while self._parent[item] != item:
            self._parent[item] = self._parent[self._parent[item]]
            item = self._parent[item]
        return item

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def _pairs_sharing_a_key(keys_by_unit: dict[int, set]) -> Iterator[tuple[int, int]]:
    """Every unordered pair of units sharing at least one key, each exactly once, lower id first.

    Replaces an all-pairs scan (2.4 billion pairs for the real corpus' event candidates) with
    per-key buckets. A pair sharing several keys is yielded only from its smallest shared key's
    bucket, which dedupes without holding a set of every pair already seen.
    """
    buckets: dict = {}
    for uid in sorted(keys_by_unit):
        for key in keys_by_unit[uid]:
            buckets.setdefault(key, []).append(uid)
    for key, members in buckets.items():
        for i in range(len(members)):
            keys_i = keys_by_unit[members[i]]
            for j in range(i + 1, len(members)):
                if min(keys_i & keys_by_unit[members[j]]) == key:
                    yield members[i], members[j]


def _resolved_entities_for_unit(mention_by_slot, resolved_by_mention, slots) -> set:
    result = set()
    for slot in slots:
        if slot is None:
            continue
        mention_id = mention_by_slot.get(slot)
        if mention_id is None:
            continue
        resolved = resolved_by_mention.get(mention_id)
        if resolved is not None:
            result.add(resolved)
    return result


def _bounds(pg_range) -> tuple[date, date] | None:
    """psycopg3 returns a DATERANGE column as its own Range type (`.lower`/`.upper`, not
    tuple-subscriptable) -- this project's only other date-span columns
    (episode.episode.start_range_low/high) deliberately used plain DATE columns instead of a
    native range type to avoid exactly this driver-level wrinkle; Phase 10's schema uses DATERANGE
    as specced, so the adaptation happens here rather than by deviating from the reviewed schema.
    """
    if pg_range is None or pg_range.isempty:
        return None
    return pg_range.lower, pg_range.upper


def _ranges_overlap(a, b) -> bool:
    a_bounds, b_bounds = _bounds(a), _bounds(b)
    if a_bounds is None or b_bounds is None:
        return True  # neutral: a missing range never blocks clustering on its own
    return a_bounds[0] < b_bounds[1] and b_bounds[0] < a_bounds[1]


def find_event_candidates(connection: Connection) -> list[dict]:
    already_decided = {
        frozenset(json.loads(row) if isinstance(row, str) else row)
        for row in connection.execute(
            text(
                "SELECT member_text_unit_ids FROM event.cluster_candidate WHERE status != 'candidate'"
            )
        ).scalars()
    }

    units = connection.execute(
        text(
            """
            SELECT g.text_unit_id, t.source, g.event_type, g.event_summary,
                   g.event_actor_slot, g.event_object_slot, g.event_location_slot,
                   tr.resolved_range, e.vector
            FROM generative.extraction g
            JOIN semantic.text_unit t ON t.id = g.text_unit_id
            LEFT JOIN event.time_resolution tr ON tr.text_unit_id = g.text_unit_id
            LEFT JOIN semantic.embedding_bge_small_en_v15 e ON e.text_unit_id = g.text_unit_id
            WHERE g.extraction_status = 'complete' AND g.event_type IS NOT NULL
            ORDER BY g.text_unit_id
            """
        )
    ).mappings().all()
    if len(units) < 2:
        return []

    mentions = connection.execute(
        text("SELECT text_unit_id, slot_index, id FROM entity.mention")
    ).all()
    mention_by_slot: dict[int, dict[int, int]] = {}
    for text_unit_id, slot_index, mention_id in mentions:
        mention_by_slot.setdefault(text_unit_id, {})[slot_index] = mention_id
    resolved_by_mention = {
        mention_id: (resolved_kind, resolved_id)
        for mention_id, resolved_kind, resolved_id in connection.execute(
            text("SELECT mention_id, resolved_kind, resolved_id FROM entity.resolved_mention")
        )
    }

    by_id = {u["text_unit_id"]: u for u in units}
    # A pair clusters only if it shares an event type AND a resolved entity, so the candidate
    # pairs are exactly those sharing an (event_type, entity) key.
    keys_by_unit = {
        uid: {
            (u["event_type"], entity)
            for entity in _resolved_entities_for_unit(
                mention_by_slot.get(uid, {}), resolved_by_mention,
                (u["event_actor_slot"], u["event_object_slot"], u["event_location_slot"]),
            )
        }
        for uid, u in by_id.items()
    }
    vectors = {uid: _parse_vector(u["vector"]) for uid, u in by_id.items() if u["vector"] is not None}
    uf = _UnionFind(by_id.keys())
    ids = list(by_id.keys())
    for a, b in _pairs_sharing_a_key(keys_by_unit):
        if not _ranges_overlap(by_id[a]["resolved_range"], by_id[b]["resolved_range"]):
            continue
        if a not in vectors or b not in vectors:
            continue
        if _cosine_similarity(vectors[a], vectors[b]) < _EMBEDDING_SIMILARITY_THRESHOLD:
            continue
        uf.union(a, b)

    components: dict[int, list[int]] = {}
    for text_unit_id in ids:
        components.setdefault(uf.find(text_unit_id), []).append(text_unit_id)

    candidates = []
    for member_ids in components.values():
        if len(member_ids) < 2:
            continue
        members = [by_id[m] for m in member_ids]
        sources = {m["source"] for m in members}
        if len(sources) < 2:
            continue
        if frozenset(member_ids) in already_decided:
            continue
        summary = next((m["event_summary"] for m in members if m["event_summary"]), None)
        ranges = [_bounds(m["resolved_range"]) for m in members]
        ranges = [r for r in ranges if r is not None]
        occurred_range = None
        if ranges:
            starts, ends = zip(*ranges, strict=True)
            occurred_range = f"[{min(starts).isoformat()},{max(ends).isoformat()})"
        candidates.append(
            {
                "event_type": members[0]["event_type"],
                "proposed_label": summary or f"{members[0]['event_type']} ({len(members)} sources)",
                "occurred_range": occurred_range,
                "member_text_unit_ids": json.dumps(sorted(member_ids)),
                "evidence": json.dumps(
                    {"sources": sorted(sources), "member_text_unit_ids": sorted(member_ids)}
                ),
                "status": "candidate",
            }
        )
    return candidates


def _clear_event_candidates(connection: Connection, partition_key: str) -> None:
    connection.execute(text("DELETE FROM event.cluster_candidate WHERE status = 'candidate'"))


@deriver(
    name="event_resolve_candidates",
    partition_class="GLOBAL",
    final_inference="COMPUTED",
    description=(
        "Cross-source event stitching via type/range/entity/embedding clustering -- proposes "
        "event.cluster_candidate rows only; event-confirm does the real multi-table write "
        "(PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md decision 12)."
    ),
    output_relation="event.cluster_candidate",
    inputs=(
        "generative_extract_text_unit", "entity_resolve_mentions",
        "event_resolve_time_expressions", "semantic_embedding_bge_small",
    ),
    clear_partition=_clear_event_candidates,
)
def event_resolve_candidates(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    candidates = find_event_candidates(connection)
    for c in candidates:
        c["computed_epoch_id"] = epoch_id
    fingerprint = fingerprint_value(sorted(c["member_text_unit_ids"] for c in candidates))
    return ComputeResult(input_fingerprint=fingerprint, rows=candidates)


# --- CLI-only: event-candidates, event-confirm, event-reject. -------------------------------


def list_event_candidates(engine: Engine, *, status: str | None = "candidate") -> list[dict]:
    query = (
        "SELECT id, event_type, proposed_label, occurred_range, member_text_unit_ids, evidence, "
        "status, created_at FROM event.cluster_candidate"
    )
    params: dict = {}
    if status is not None:
        query += " WHERE status = :status"
        params["status"] = status
    query += " ORDER BY id"
    with engine.connect() as connection:
        rows = connection.execute(text(query), params).mappings().all()
    return [dict(row) for row in rows]


def confirm_event(engine: Engine, candidate_id: int) -> dict:
    with engine.begin() as connection:
        candidate = connection.execute(
            text(
                "SELECT event_type, proposed_label, occurred_range, member_text_unit_ids, status "
                "FROM event.cluster_candidate WHERE id = :id FOR UPDATE"
            ),
            {"id": candidate_id},
        ).mappings().first()
        if candidate is None:
            raise ValueError(f"no event candidate with id {candidate_id}")
        if candidate["status"] != "candidate":
            raise ValueError(f"event candidate {candidate_id} is already status={candidate['status']!r}")

        canonical = connection.execute(
            text(
                "INSERT INTO event.canonical_event (event_type, label, occurred_range) "
                "VALUES (:event_type, :label, :occurred_range) "
                "RETURNING id, event_type, label, occurred_range, created_at"
            ),
            {
                "event_type": candidate["event_type"],
                "label": candidate["proposed_label"],
                "occurred_range": candidate["occurred_range"],
            },
        ).mappings().one()
        member_ids = candidate["member_text_unit_ids"]
        for text_unit_id in member_ids:
            connection.execute(
                text(
                    "INSERT INTO event.event_link (canonical_event_id, text_unit_id) "
                    "VALUES (:event_id, :text_unit_id)"
                ),
                {"event_id": canonical["id"], "text_unit_id": text_unit_id},
            )
        connection.execute(
            text("UPDATE event.cluster_candidate SET status = 'confirmed' WHERE id = :id"),
            {"id": candidate_id},
        )
        return {**dict(canonical), "member_text_unit_ids": member_ids}


def reject_event(engine: Engine, candidate_id: int) -> dict:
    with engine.begin() as connection:
        row = connection.execute(
            text(
                "SELECT status FROM event.cluster_candidate WHERE id = :id FOR UPDATE"
            ),
            {"id": candidate_id},
        ).first()
        if row is None:
            raise ValueError(f"no event candidate with id {candidate_id}")
        if row.status != "candidate":
            raise ValueError(f"event candidate {candidate_id} is already status={row.status!r}")
        updated = connection.execute(
            text(
                "UPDATE event.cluster_candidate SET status = 'rejected' WHERE id = :id "
                "RETURNING id, status"
            ),
            {"id": candidate_id},
        ).mappings().one()
        return dict(updated)
