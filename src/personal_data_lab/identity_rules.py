"""Phase 2a rules tier (PHASE2_IMPLEMENTATION_PLAN.md): auto-link only on a normalized email or
phone exact match across two or more (source, identity_value) pairs. Never on name equality --
see the plan's Decisions for why that's queued for review instead (identity_candidates.py).

Safe to re-run: an already-linked pair is skipped, and a not_same_as declaration on any member of
a group removes that member from consideration entirely, not just from the auto-link path.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .identity import create_person, merge_identity
from .identity_normalize import classify, normalize_email, normalize_phone

_ALL_IDENTITIES_SQL = """
    SELECT DISTINCT source, actor_identity AS identity_value
    FROM events WHERE actor_identity IS NOT NULL
    UNION
    SELECT DISTINCT source, actor_identity AS identity_value
    FROM records WHERE actor_identity IS NOT NULL
"""


@dataclass(frozen=True)
class RulesLink:
    normalized_value: str
    shape: str
    members: tuple[tuple[str, str], ...]
    person_id: int
    created_person: bool


@dataclass(frozen=True)
class RulesConflict:
    """A cross-source group already actively linked to more than one person. The rules tier
    never silently picks one -- this is reported back to the caller (and, from the CLI, printed
    and turned into a non-zero exit) instead.
    """

    normalized_value: str
    shape: str
    members: tuple[tuple[str, str], ...]
    person_ids: tuple[int, ...]


@dataclass(frozen=True)
class RulesOutcome:
    links: tuple[RulesLink, ...]
    conflicts: tuple[RulesConflict, ...]


def _normalize(value: str, shape: str) -> str:
    return normalize_email(value) if shape == "email" else normalize_phone(value)


def _already_governed(connection, source: str, identity_value: str) -> tuple[int | None, bool]:
    person_id = connection.execute(
        text(
            "SELECT person_id FROM identity_links WHERE source = :source "
            "AND identity_value = :value AND lifecycle_state = 'active'"
        ),
        {"source": source, "value": identity_value},
    ).scalar_one_or_none()
    has_not_same_as = connection.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM identity.not_same_as "
            "WHERE source = :source AND identity_value = :value)"
        ),
        {"source": source, "value": identity_value},
    ).scalar_one()
    return person_id, bool(has_not_same_as)


def run_rules_tier(engine: Engine, *, dry_run: bool = False) -> RulesOutcome:
    groups: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    with engine.connect() as connection:
        for source, identity_value in connection.execute(text(_ALL_IDENTITIES_SQL)).all():
            shape = classify(identity_value)
            if shape not in ("email", "phone"):
                continue
            groups[(shape, _normalize(identity_value, shape))].append((source, identity_value))

    links: list[RulesLink] = []
    conflicts: list[RulesConflict] = []

    with engine.connect() as connection:
        for (shape, normalized), members in groups.items():
            if len({source for source, _ in members}) < 2:
                continue  # only interesting once it corroborates across more than one source

            eligible: list[tuple[str, str]] = []
            existing_person_ids: set[int] = set()
            blocked = False
            for source, identity_value in members:
                person_id, has_not_same_as = _already_governed(connection, source, identity_value)
                if has_not_same_as:
                    blocked = True
                    break
                eligible.append((source, identity_value))
                if person_id is not None:
                    existing_person_ids.add(person_id)

            if blocked or len({source for source, _ in eligible}) < 2:
                continue

            if len(existing_person_ids) > 1:
                conflicts.append(
                    RulesConflict(
                        normalized_value=normalized,
                        shape=shape,
                        members=tuple(eligible),
                        person_ids=tuple(sorted(existing_person_ids)),
                    )
                )
                continue

            if dry_run:
                links.append(
                    RulesLink(
                        normalized_value=normalized,
                        shape=shape,
                        members=tuple(eligible),
                        person_id=next(iter(existing_person_ids), -1),
                        created_person=not existing_person_ids,
                    )
                )
                continue

            created_person = not existing_person_ids
            if created_person:
                _, display_value = eligible[0]
                person_id = create_person(engine, display_value).id
            else:
                person_id = next(iter(existing_person_ids))

            for source, identity_value in eligible:
                merge_identity(
                    engine,
                    person_id,
                    source,
                    identity_value,
                    note=f"rules tier: exact {shape} match ({normalized})",
                    tier="computed",
                    evidence={"rule": f"exact_{shape}_match", "normalized_value": normalized},
                )

            links.append(
                RulesLink(
                    normalized_value=normalized,
                    shape=shape,
                    members=tuple(eligible),
                    person_id=person_id,
                    created_person=created_person,
                )
            )

    return RulesOutcome(links=tuple(links), conflicts=tuple(conflicts))
