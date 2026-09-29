"""Phase 10's entity/relation layer (PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md decisions 3-5, 8,
14): explodes generative.extraction's raw entity_mentions/relation_candidates JSONB into real rows,
then proposes (never auto-applies) resolutions for what a mention actually refers to.

Three derivers plus direct CLI-only writes, mirroring identity.py's own precedent for `people`
(never written by a deriver, only by `identity-create-person`) and episode.py's candidate/confirm
shape:
- `entity_mention_extract` (LOCAL): pure explode, one row per (text_unit_id, slot_index).
- `relation_candidate_extract` (LOCAL): explode plus decision 8's three hardening filters, applied
  here rather than in generative.py -- generative.extraction is the model's raw, never-altered
  output (decision 11); this deriver is what actually decides which relation tuples become rows.
- `entity_resolve_mentions` (GLOBAL): proposes entity.link_candidate rows only -- never writes
  entity.entity_link/entity.person_link directly (the real v2 bug decision 14 fixed). Exact-match
  only for now: fuzzy matching is explicitly deferred in the plan's own runtime notes until real
  mention data shows it's needed, and no real mention data exists yet (Phase 10 hasn't been
  rolled out against the canonical database).

entity-create/entity-confirm/entity-reject/entity-merge/entity-split are plain functions the CLI
calls directly, not derivers -- same precedent identity.py's create_person/merge_identity/
split_identity already established: a human decision isn't itself a derivation.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ...fingerprints import fingerprint_value
from ..partitions import parse_local_key
from ..registry import ComputeResult, deriver

logger = logging.getLogger(__name__)

# Decision 8: the three interpretation-heaviest relation types require explicit textual evidence
# to be stored at all -- an implied causal/stance claim is exactly the kind of overreach that
# erodes trust in this layer.
_EXPLICIT_ONLY_RELATION_TYPES = frozenset({"influenced", "supports", "opposes"})


# --- entity_mention_extract: pure explode, no resolution. -----------------------------------


def _clear_mentions(connection: Connection, partition_key: str) -> None:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        return
    connection.execute(
        text(
            """
            DELETE FROM entity.mention
            WHERE text_unit_id IN (
                SELECT id FROM semantic.text_unit
                WHERE source = :source AND started_at >= :start AND started_at < :end
            )
            """
        ),
        {"source": source, "end": end, "start": start},
    )


@deriver(
    name="entity_mention_extract",
    partition_class="LOCAL",
    final_inference="COMPUTED",
    description=(
        "Explodes generative.extraction.entity_mentions into one entity.mention row per "
        "occurrence (PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md decision 4)."
    ),
    output_relation="entity.mention",
    inputs=("generative_extract_text_unit",),
    clear_partition=_clear_mentions,
)
def entity_mention_extract(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        return ComputeResult(input_fingerprint=fingerprint_value(["undated"]), rows=[])

    extractions = connection.execute(
        text(
            """
            SELECT g.text_unit_id, g.entity_mentions
            FROM generative.extraction g
            JOIN semantic.text_unit t ON t.id = g.text_unit_id
            WHERE t.source = :source AND t.started_at >= :start AND t.started_at < :end
            AND g.extraction_status = 'complete'
            ORDER BY g.text_unit_id
            """
        ),
        {"source": source, "start": start, "end": end},
    ).all()

    rows = []
    fingerprint_source = []
    for text_unit_id, entity_mentions in extractions:
        fingerprint_source.append([text_unit_id, entity_mentions])
        for slot_index, mention in enumerate(entity_mentions):
            rows.append(
                {
                    "text_unit_id": text_unit_id,
                    "slot_index": slot_index,
                    "surface_text": mention["mention"],
                    "kind": mention["kind"],
                    "computed_epoch_id": epoch_id,
                }
            )
    return ComputeResult(input_fingerprint=fingerprint_value(fingerprint_source), rows=rows)


# --- relation_candidate_extract: explode plus decision 8's hardening filters. ----------------


def _clear_relation_candidates(connection: Connection, partition_key: str) -> None:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        return
    connection.execute(
        text(
            """
            DELETE FROM entity.relation_candidate
            WHERE text_unit_id IN (
                SELECT id FROM semantic.text_unit
                WHERE source = :source AND started_at >= :start AND started_at < :end
            )
            """
        ),
        {"source": source, "end": end, "start": start},
    )


@deriver(
    name="relation_candidate_extract",
    partition_class="LOCAL",
    final_inference="COMPUTED",
    description=(
        "Explodes generative.extraction.relation_candidates into entity.relation_candidate rows, "
        "applying decision 8's slot-validity and evidence-mode hardening before a row is ever "
        "written (PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md decision 8)."
    ),
    output_relation="entity.relation_candidate",
    inputs=("generative_extract_text_unit",),
    clear_partition=_clear_relation_candidates,
)
def relation_candidate_extract(
    connection: Connection, partition_key: str, epoch_id: int
) -> ComputeResult:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        return ComputeResult(input_fingerprint=fingerprint_value(["undated"]), rows=[])

    extractions = connection.execute(
        text(
            """
            SELECT g.text_unit_id, g.entity_mentions, g.relation_candidates
            FROM generative.extraction g
            JOIN semantic.text_unit t ON t.id = g.text_unit_id
            WHERE t.source = :source AND t.started_at >= :start AND t.started_at < :end
            AND g.extraction_status = 'complete'
            ORDER BY g.text_unit_id
            """
        ),
        {"source": source, "start": start, "end": end},
    ).all()

    rows = []
    fingerprint_source = []
    dropped = 0
    for text_unit_id, entity_mentions, relation_candidates in extractions:
        fingerprint_source.append([text_unit_id, relation_candidates])
        n_entities = len(entity_mentions)
        for tuple_ in relation_candidates:
            subject_slot, object_slot = tuple_["subject_slot"], tuple_["object_slot"]
            relation_type, evidence_mode = tuple_["relation_type"], tuple_["evidence_mode"]
            if subject_slot == object_slot:
                logger.warning(
                    "relation_candidate_extract: dropping self-referencing tuple on text_unit %s "
                    "(slot %s)", text_unit_id, subject_slot,
                )
                dropped += 1
                continue
            if not (0 <= subject_slot < n_entities and 0 <= object_slot < n_entities):
                logger.warning(
                    "relation_candidate_extract: dropping out-of-range tuple on text_unit %s "
                    "(subject=%s object=%s, %s entities)",
                    text_unit_id, subject_slot, object_slot, n_entities,
                )
                dropped += 1
                continue
            if relation_type in _EXPLICIT_ONLY_RELATION_TYPES and evidence_mode != "explicit":
                logger.warning(
                    "relation_candidate_extract: dropping implied %s tuple on text_unit %s "
                    "(evidence_mode must be explicit for this relation type)",
                    relation_type, text_unit_id,
                )
                dropped += 1
                continue
            rows.append(
                {
                    "text_unit_id": text_unit_id,
                    "subject_slot": subject_slot,
                    "relation_type": relation_type,
                    "object_slot": object_slot,
                    "evidence_mode": evidence_mode,
                    "computed_epoch_id": epoch_id,
                }
            )
    if dropped:
        logger.warning("relation_candidate_extract: dropped %d invalid relation tuple(s)", dropped)
    return ComputeResult(input_fingerprint=fingerprint_value(fingerprint_source), rows=rows)


# --- entity_resolve_mentions: proposes entity.link_candidate rows only. ----------------------


def _normalize(surface_text: str) -> str:
    return surface_text.strip().casefold()


def _clear_link_candidates(connection: Connection, partition_key: str) -> None:
    # Only ever removes still-undecided candidates -- a confirmed or rejected row is a permanent
    # human decision, never silently regenerated (same precedent episode.py's own
    # _clear_detected_candidates established after a real disposability-rebuild bug).
    connection.execute(text("DELETE FROM entity.link_candidate WHERE status = 'candidate'"))


@deriver(
    name="entity_resolve_mentions",
    partition_class="GLOBAL",
    final_inference="COMPUTED",
    description=(
        "Proposes exact-match entity.link_candidate rows for unresolved mentions -- never "
        "writes an active link directly (PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md decision 14)."
    ),
    output_relation="entity.link_candidate",
    inputs=("entity_mention_extract",),
    clear_partition=_clear_link_candidates,
)
def entity_resolve_mentions(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    already_handled = {
        row[0]
        for row in connection.execute(
            text(
                "SELECT mention_id FROM entity.link_candidate "
                "UNION SELECT mention_id FROM entity.entity_link "
                "UNION SELECT mention_id FROM entity.person_link"
            )
        )
    }

    mentions = connection.execute(
        text("SELECT id, surface_text, kind FROM entity.mention ORDER BY id")
    ).all()

    people_by_norm: dict[str, int] = {}
    for person_id, display_name in connection.execute(text("SELECT id, display_name FROM people")):
        people_by_norm.setdefault(_normalize(display_name), person_id)

    entities_by_norm: dict[tuple[str, str], int] = {}
    for entity_id, kind, canonical_name in connection.execute(
        text("SELECT id, kind, canonical_name FROM entity.entity")
    ):
        entities_by_norm.setdefault((kind, _normalize(canonical_name)), entity_id)

    rows = []
    for mention_id, surface_text, kind in mentions:
        if mention_id in already_handled:
            continue
        normalized = _normalize(surface_text)
        if kind == "person":
            person_id = people_by_norm.get(normalized)
            if person_id is None:
                continue
            rows.append(
                {
                    "mention_id": mention_id,
                    "candidate_person_id": person_id,
                    "candidate_entity_id": None,
                    "resolution_tier": "exact_match",
                    "evidence": json.dumps({"surface_text": surface_text, "matched": "people.display_name"}),
                    "status": "candidate",
                    "computed_epoch_id": epoch_id,
                }
            )
        else:
            entity_id = entities_by_norm.get((kind, normalized))
            if entity_id is None:
                continue
            rows.append(
                {
                    "mention_id": mention_id,
                    "candidate_person_id": None,
                    "candidate_entity_id": entity_id,
                    "resolution_tier": "exact_match",
                    "evidence": json.dumps({"surface_text": surface_text, "matched": "entity.entity.canonical_name"}),
                    "status": "candidate",
                    "computed_epoch_id": epoch_id,
                }
            )
    fingerprint = fingerprint_value(
        sorted((r["mention_id"], r["candidate_person_id"], r["candidate_entity_id"]) for r in rows)
    )
    return ComputeResult(input_fingerprint=fingerprint, rows=rows)


# --- CLI-only functions: entity-create, entity-candidates, entity-confirm, entity-reject, ----
# --- entity-merge, entity-split. Never called by a deriver. ----------------------------------


def create_entity(engine: Engine, kind: str, canonical_name: str) -> dict:
    with engine.begin() as connection:
        row = connection.execute(
            text(
                "INSERT INTO entity.entity (kind, canonical_name) VALUES (:kind, :name) "
                "RETURNING id, kind, canonical_name, created_at"
            ),
            {"kind": kind, "name": canonical_name},
        ).mappings().one()
    return dict(row)


def list_link_candidates(engine: Engine, *, status: str | None = "candidate") -> list[dict]:
    query = (
        "SELECT id, mention_id, candidate_person_id, candidate_entity_id, resolution_tier, "
        "evidence, status, created_at FROM entity.link_candidate"
    )
    params: dict = {}
    if status is not None:
        query += " WHERE status = :status"
        params["status"] = status
    query += " ORDER BY id"
    with engine.connect() as connection:
        rows = connection.execute(text(query), params).mappings().all()
    return [dict(row) for row in rows]


def _resolve_pending_candidate(connection: Connection, candidate_id: int):
    row = connection.execute(
        text(
            "SELECT mention_id, candidate_person_id, candidate_entity_id, evidence, status "
            "FROM entity.link_candidate WHERE id = :id FOR UPDATE"
        ),
        {"id": candidate_id},
    ).mappings().first()
    if row is None:
        raise ValueError(f"no link candidate with id {candidate_id}")
    if row["status"] != "candidate":
        raise ValueError(f"link candidate {candidate_id} is already status={row['status']!r}")
    return row


def confirm_entity_link(engine: Engine, candidate_id: int) -> dict:
    with engine.begin() as connection:
        candidate = _resolve_pending_candidate(connection, candidate_id)
        evidence = json.dumps(candidate["evidence"])
        if candidate["candidate_person_id"] is not None:
            written = connection.execute(
                text(
                    "INSERT INTO entity.person_link (mention_id, person_id, evidence) "
                    "VALUES (:mention_id, :person_id, CAST(:evidence AS jsonb)) "
                    "RETURNING id, mention_id, person_id, lifecycle_state, created_at"
                ),
                {
                    "mention_id": candidate["mention_id"],
                    "person_id": candidate["candidate_person_id"],
                    "evidence": evidence,
                },
            ).mappings().one()
        else:
            written = connection.execute(
                text(
                    "INSERT INTO entity.entity_link (mention_id, entity_id, evidence) "
                    "VALUES (:mention_id, :entity_id, CAST(:evidence AS jsonb)) "
                    "RETURNING id, mention_id, entity_id, lifecycle_state, created_at"
                ),
                {
                    "mention_id": candidate["mention_id"],
                    "entity_id": candidate["candidate_entity_id"],
                    "evidence": evidence,
                },
            ).mappings().one()
        connection.execute(
            text("UPDATE entity.link_candidate SET status = 'confirmed' WHERE id = :id"),
            {"id": candidate_id},
        )
        return dict(written)


def reject_entity_link(engine: Engine, candidate_id: int) -> dict:
    with engine.begin() as connection:
        _resolve_pending_candidate(connection, candidate_id)
        updated = connection.execute(
            text(
                "UPDATE entity.link_candidate SET status = 'rejected' WHERE id = :id "
                "RETURNING id, mention_id, status"
            ),
            {"id": candidate_id},
        ).mappings().one()
        return dict(updated)


def merge_entity(engine: Engine, mention_id: int, *, person_id: int | None = None, entity_id: int | None = None) -> dict:
    """Direct human-asserted link for a mention the resolver never proposed a candidate for --
    same precedent identity.py's merge_identity establishes for provider identities. Exactly one
    of person_id/entity_id must be given.
    """
    if (person_id is None) == (entity_id is None):
        raise ValueError("exactly one of person_id or entity_id must be given")
    evidence = json.dumps({"resolution_tier": "manual"})
    with engine.begin() as connection:
        if person_id is not None:
            written = connection.execute(
                text(
                    "INSERT INTO entity.person_link (mention_id, person_id, evidence) "
                    "VALUES (:mention_id, :person_id, CAST(:evidence AS jsonb)) "
                    "RETURNING id, mention_id, person_id, lifecycle_state, created_at"
                ),
                {"mention_id": mention_id, "person_id": person_id, "evidence": evidence},
            ).mappings().one()
        else:
            written = connection.execute(
                text(
                    "INSERT INTO entity.entity_link (mention_id, entity_id, evidence) "
                    "VALUES (:mention_id, :entity_id, CAST(:evidence AS jsonb)) "
                    "RETURNING id, mention_id, entity_id, lifecycle_state, created_at"
                ),
                {"mention_id": mention_id, "entity_id": entity_id, "evidence": evidence},
            ).mappings().one()
        return dict(written)


def split_entity_link(engine: Engine, mention_id: int) -> dict:
    """Revert whichever active link (person or entity) exists for mention_id -- reversible,
    never a delete, same precedent identity.py's split_identity establishes.
    """
    with engine.begin() as connection:
        for table, id_col in (("entity.person_link", "person_id"), ("entity.entity_link", "entity_id")):
            row = connection.execute(
                text(
                    f"UPDATE {table} SET lifecycle_state = 'reverted', reverted_at = CURRENT_TIMESTAMP "
                    f"WHERE mention_id = :mention_id AND lifecycle_state = 'active' "
                    f"RETURNING id, mention_id, {id_col}, lifecycle_state, reverted_at"
                ),
                {"mention_id": mention_id},
            ).mappings().first()
            if row is not None:
                return dict(row)
        raise ValueError(f"no active link for mention {mention_id}")
