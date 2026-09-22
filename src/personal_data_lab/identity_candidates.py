"""Cross-source name-match candidate queue (PHASE2_IMPLEMENTATION_PLAN.md): the one deterministic
signal available today to feed the review queue the Splink/STATISTICAL tier would otherwise
supply. Deliberately narrow -- see the plan's Decisions for why exact name equality is queued for
review rather than auto-linked like the rules tier's email/phone matches.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .identity_normalize import classify

_ALL_IDENTITIES_SQL = """
    SELECT DISTINCT source, actor_identity AS identity_value
    FROM events WHERE actor_identity IS NOT NULL
    UNION
    SELECT DISTINCT source, actor_identity AS identity_value
    FROM records WHERE actor_identity IS NOT NULL
"""


@dataclass(frozen=True)
class NameCandidate:
    normalized_name: str
    members: tuple[tuple[str, str], ...]


def cross_source_name_candidates(engine: Engine) -> tuple[NameCandidate, ...]:
    """Exact normalized-name matches across two or more sources, excluding role labels, values
    already actively linked on every member, and any member with a not_same_as declaration.
    """
    groups: dict[str, list[tuple[str, str]]] = {}
    with engine.connect() as connection:
        for source, identity_value in connection.execute(text(_ALL_IDENTITIES_SQL)).all():
            if classify(identity_value) != "name":
                continue
            normalized = " ".join(identity_value.strip().lower().split())
            groups.setdefault(normalized, []).append((source, identity_value))

        not_same_as_pairs = {
            (row.source, row.identity_value)
            for row in connection.execute(
                text("SELECT DISTINCT source, identity_value FROM identity.not_same_as")
            ).all()
        }
        already_linked = {
            (row.source, row.identity_value)
            for row in connection.execute(
                text(
                    "SELECT DISTINCT source, identity_value FROM identity_links "
                    "WHERE lifecycle_state = 'active'"
                )
            ).all()
        }

    candidates = []
    for normalized, members in groups.items():
        filtered = [pair for pair in members if pair not in not_same_as_pairs]
        if len({source for source, _ in filtered}) < 2:
            continue
        if all(pair in already_linked for pair in filtered):
            continue
        candidates.append(NameCandidate(normalized_name=normalized, members=tuple(filtered)))

    return tuple(sorted(candidates, key=lambda c: c.normalized_name))
