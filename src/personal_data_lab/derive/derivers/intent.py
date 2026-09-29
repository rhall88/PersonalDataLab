"""Phase 10's decision/open-loop layer (PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md decision 13):
the same real gap event resolution had (a per-occurrence state with no mechanism clustering which
occurrences are the same thread), fixed the same way -- a candidate/confirm pair, not a hard
clustering rule. `intent_resolve_candidates` clusters same-kind occurrences by resolved-entity
overlap and embedding similarity, orders them chronologically, and flags (never blocks on) an
implausible state-transition sequence -- a real, human-reviewable signal per decision 13's own
framing, not a hard rule.
"""

from __future__ import annotations

import json
from itertools import pairwise

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ...fingerprints import fingerprint_value
from ..registry import ComputeResult, deriver
from .event import _cosine_similarity, _pairs_sharing_a_key, _parse_vector, _UnionFind

# Same placeholder-pending-real-grounding caveat as event.py's own threshold: no real rollout data
# exists yet to tune this against.
_EMBEDDING_SIMILARITY_THRESHOLD = 0.6

_DECISION_RANK = {"considering": 0, "decided": 1, "implemented": 2}
_DECISION_TERMINAL = {"abandoned", "reversed"}
_OPEN_LOOP_RANK = {"opened": 0, "continued": 1}
_OPEN_LOOP_TERMINAL = {"resolved", "abandoned"}


def _is_plausible_sequence(kind: str, states: list[str]) -> bool:
    rank, terminal = (
        (_DECISION_RANK, _DECISION_TERMINAL) if kind == "decision" else (_OPEN_LOOP_RANK, _OPEN_LOOP_TERMINAL)
    )
    for previous, current in pairwise(states):
        if previous in terminal:
            return False  # nothing legitimately follows a terminal state
        if current in terminal:
            continue  # a terminal state can end the thread at any point
        if rank.get(current, -1) < rank.get(previous, -1):
            return False
    return True


def _resolved_entities_by_text_unit(connection: Connection) -> dict[int, set]:
    entities: dict[int, set] = {}
    for text_unit_id, kind, entity_id in connection.execute(
        text(
            """
            SELECT m.text_unit_id, rm.resolved_kind, rm.resolved_id
            FROM entity.mention m
            JOIN entity.resolved_mention rm ON rm.mention_id = m.id
            """
        )
    ):
        entities.setdefault(text_unit_id, set()).add((kind, entity_id))
    return entities


def _find_thread_candidates(connection: Connection, kind: str) -> list[dict]:
    state_column = "decision_state" if kind == "decision" else "open_loop_state"
    summary_column = "decision_summary" if kind == "decision" else "open_loop_summary"

    already_decided = {
        frozenset(json.loads(row) if isinstance(row, str) else row)
        for row in connection.execute(
            text(
                "SELECT member_text_unit_ids FROM intent.cluster_candidate "
                "WHERE kind = :kind AND status != 'candidate'"
            ),
            {"kind": kind},
        ).scalars()
    }

    units = connection.execute(
        text(
            f"""
            SELECT g.text_unit_id, t.source, t.started_at, g.{state_column} AS state,
                   g.{summary_column} AS summary, e.vector
            FROM generative.extraction g
            JOIN semantic.text_unit t ON t.id = g.text_unit_id
            LEFT JOIN semantic.embedding_bge_small_en_v15 e ON e.text_unit_id = g.text_unit_id
            WHERE g.extraction_status = 'complete' AND g.{state_column} != 'none'
            ORDER BY g.text_unit_id
            """
        )
    ).mappings().all()
    if len(units) < 2:
        return []

    by_id = {u["text_unit_id"]: u for u in units}
    resolved = _resolved_entities_by_text_unit(connection)
    entities_by_unit = {uid: resolved.get(uid, set()) for uid in by_id}
    vectors = {
        uid: _parse_vector(u["vector"]) for uid, u in by_id.items() if u["vector"] is not None
    }

    uf = _UnionFind(by_id.keys())
    ids = list(by_id.keys())
    for u1, u2 in _pairs_sharing_a_key(entities_by_unit):
        if u1 not in vectors or u2 not in vectors:
            continue
        if _cosine_similarity(vectors[u1], vectors[u2]) < _EMBEDDING_SIMILARITY_THRESHOLD:
            continue
        uf.union(u1, u2)

    components: dict[int, list[int]] = {}
    for uid in ids:
        components.setdefault(uf.find(uid), []).append(uid)

    candidates = []
    for member_ids in components.values():
        if len(member_ids) < 2:
            continue
        if frozenset(member_ids) in already_decided:
            continue
        members = sorted((by_id[m] for m in member_ids), key=lambda u: u["started_at"])
        states = [m["state"] for m in members]
        label = next((m["summary"] for m in members if m["summary"]), f"{kind} thread")
        candidates.append(
            {
                "kind": kind,
                "proposed_label": label,
                "member_text_unit_ids": json.dumps([m["text_unit_id"] for m in members]),
                "evidence": json.dumps(
                    {
                        "state_sequence": [
                            {
                                "text_unit_id": m["text_unit_id"],
                                "source": m["source"],
                                "state": m["state"],
                                "started_at": m["started_at"].isoformat(),
                            }
                            for m in members
                        ],
                        "transition_plausible": _is_plausible_sequence(kind, states),
                    }
                ),
                "status": "candidate",
            }
        )
    return candidates


def _clear_intent_candidates(connection: Connection, partition_key: str) -> None:
    connection.execute(text("DELETE FROM intent.cluster_candidate WHERE status = 'candidate'"))


@deriver(
    name="intent_resolve_candidates",
    partition_class="GLOBAL",
    final_inference="COMPUTED",
    description=(
        "Clusters decision/open-loop occurrences into cross-occurrence threads by resolved-entity "
        "overlap and embedding similarity, flagging (not blocking on) implausible state-transition "
        "sequences (PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md decision 13)."
    ),
    output_relation="intent.cluster_candidate",
    inputs=("generative_extract_text_unit", "entity_resolve_mentions", "semantic_embedding_bge_small"),
    clear_partition=_clear_intent_candidates,
)
def intent_resolve_candidates(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    candidates = _find_thread_candidates(connection, "decision") + _find_thread_candidates(
        connection, "open_loop"
    )
    for c in candidates:
        c["computed_epoch_id"] = epoch_id
    fingerprint = fingerprint_value(sorted(c["member_text_unit_ids"] for c in candidates))
    return ComputeResult(input_fingerprint=fingerprint, rows=candidates)


# --- CLI-only: intent-candidates, intent-confirm, intent-reject. ----------------------------


def list_intent_candidates(engine: Engine, *, kind: str | None = None, status: str | None = "candidate") -> list[dict]:
    query = (
        "SELECT id, kind, proposed_label, member_text_unit_ids, evidence, status, created_at "
        "FROM intent.cluster_candidate"
    )
    clauses, params = [], {}
    if kind is not None:
        clauses.append("kind = :kind")
        params["kind"] = kind
    if status is not None:
        clauses.append("status = :status")
        params["status"] = status
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id"
    with engine.connect() as connection:
        rows = connection.execute(text(query), params).mappings().all()
    return [dict(row) for row in rows]


def confirm_intent(engine: Engine, candidate_id: int) -> dict:
    with engine.begin() as connection:
        candidate = connection.execute(
            text(
                "SELECT kind, proposed_label, member_text_unit_ids, status "
                "FROM intent.cluster_candidate WHERE id = :id FOR UPDATE"
            ),
            {"id": candidate_id},
        ).mappings().first()
        if candidate is None:
            raise ValueError(f"no intent candidate with id {candidate_id}")
        if candidate["status"] != "candidate":
            raise ValueError(f"intent candidate {candidate_id} is already status={candidate['status']!r}")

        thread = connection.execute(
            text(
                "INSERT INTO intent.thread (kind, label) VALUES (:kind, :label) "
                "RETURNING id, kind, label, created_at"
            ),
            {"kind": candidate["kind"], "label": candidate["proposed_label"]},
        ).mappings().one()
        member_ids = candidate["member_text_unit_ids"]
        for text_unit_id in member_ids:
            connection.execute(
                text(
                    "INSERT INTO intent.thread_link (thread_id, text_unit_id) "
                    "VALUES (:thread_id, :text_unit_id)"
                ),
                {"thread_id": thread["id"], "text_unit_id": text_unit_id},
            )
        connection.execute(
            text("UPDATE intent.cluster_candidate SET status = 'confirmed' WHERE id = :id"),
            {"id": candidate_id},
        )
        return {**dict(thread), "member_text_unit_ids": member_ids}


def reject_intent(engine: Engine, candidate_id: int) -> dict:
    with engine.begin() as connection:
        row = connection.execute(
            text("SELECT status FROM intent.cluster_candidate WHERE id = :id FOR UPDATE"),
            {"id": candidate_id},
        ).first()
        if row is None:
            raise ValueError(f"no intent candidate with id {candidate_id}")
        if row.status != "candidate":
            raise ValueError(f"intent candidate {candidate_id} is already status={row.status!r}")
        updated = connection.execute(
            text(
                "UPDATE intent.cluster_candidate SET status = 'rejected' WHERE id = :id "
                "RETURNING id, status"
            ),
            {"id": candidate_id},
        ).mappings().one()
        return dict(updated)
