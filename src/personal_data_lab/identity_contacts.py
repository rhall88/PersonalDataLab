"""Contact-card identity bridge (identity bootstrap).

Facebook identifies correspondents by display name and iMessage by phone number, so the Phase 2
rules tier (exact phone/email equality across sources) can never connect the two. Contact cards
hold both a name and phone numbers/emails, which makes them the bridge.

This module only *proposes*: each proposal is one contact (duplicate cards sharing a phone or
email are merged) plus every provider identity it matches, ranked by message volume. Nothing is
linked until a person confirms a proposal, and confirming goes through the existing
`merge_identity` (append-only assertion log, reversible via `split_identity`). Phone and email
matches are exact; name matches are exact after casefolding, and a name matching two different
contacts is dropped as ambiguous rather than guessed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .identity import create_person, merge_identity
from .identity_normalize import classify, normalize_email, normalize_name, phone_key

MatchKind = Literal["phone", "email", "name"]


@dataclass(frozen=True)
class ContactMatch:
    source: str
    identity_value: str
    match: MatchKind
    events: int
    linked_person_id: int | None


@dataclass(frozen=True)
class ContactProposal:
    key: str
    display_name: str
    aliases: tuple[str, ...]
    card_record_ids: tuple[int, ...]
    matches: tuple[ContactMatch, ...]
    total_events: int
    existing_person_ids: tuple[int, ...]


@dataclass(frozen=True)
class ConfirmResult:
    key: str
    person_id: int
    created_person: bool
    linked: tuple[tuple[str, str], ...]
    already_linked: tuple[tuple[str, str], ...]
    skipped_conflicts: tuple[tuple[str, str, int], ...]
    aliases_added: tuple[str, ...]


@dataclass
class _Card:
    record_id: int
    names: list[str]
    given: str | None
    phones: set[str]
    emails: set[str]


def _values(raw) -> list[str]:
    """A property's values as strings. Google cards wrap each one as {"value": ..., "params": ...};
    Apple cards store bare strings."""
    items = raw if isinstance(raw, list) else [raw]
    out = []
    for item in items:
        if isinstance(item, dict):
            item = item.get("value")
        if item not in (None, ""):
            out.append(str(item))
    return out


def _property(props: dict, name: str) -> list[str]:
    """Every value of a vCard property, including grouped forms like ITEM3.TEL."""
    out: list[str] = []
    for key, raw in props.items():
        upper = key.upper()
        if upper == name or upper.endswith("." + name):
            out.extend(_values(raw))
    return out


def _parse_device_card(record_id: int, card: dict) -> _Card | None:
    """A contact imported from the phone's address book (ios_backup/contacts.py): already
    structured, no vCard properties."""
    names = [n.strip() for n in card.get("names") or [] if n and n.strip()]
    phones = {k for k in (phone_key(p.get("value", "")) for p in card.get("phones") or []) if k}
    emails = {
        normalize_email(e["value"]) for e in card.get("emails") or [] if "@" in e.get("value", "")
    }
    if not names and not phones and not emails:
        return None
    return _Card(
        record_id=record_id, names=names, given=card.get("given") or None, phones=phones, emails=emails
    )


def _parse_card(record_id: int, metadata: dict) -> _Card | None:
    if isinstance(metadata.get("ios_contact"), dict):
        return _parse_device_card(record_id, metadata["ios_contact"])
    props = (metadata.get("apple") or {}).get("properties") or metadata.get("google") or {}
    if not isinstance(props, dict):
        return None
    names = [n.strip() for n in _property(props, "FN") if n.strip()]
    given = None
    for structured in _property(props, "N"):
        parts = structured.split(";")
        family = parts[0].strip() if parts else ""
        given = parts[1].strip() if len(parts) > 1 and parts[1].strip() else given
        full = " ".join(p for p in (given or "", family) if p)
        if full:
            names.append(full)
    phones = {k for k in (phone_key(v) for v in _property(props, "TEL")) if k}
    emails = {normalize_email(v) for v in _property(props, "EMAIL") if "@" in v}
    if not names and not phones and not emails:
        return None
    return _Card(record_id=record_id, names=names, given=given, phones=phones, emails=emails)


def _load_cards(connection: Connection) -> list[_Card]:
    rows = connection.execute(
        text("SELECT id, metadata FROM records WHERE record_type = 'contact' ORDER BY id")
    ).all()
    cards = []
    for record_id, metadata in rows:
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        card = _parse_card(record_id, metadata or {})
        if card is not None:
            cards.append(card)
    return cards


def _group_cards(cards: list[_Card]) -> list[list[_Card]]:
    """Duplicate cards for one person (an Apple copy and a Google copy) share a phone or email."""
    parent = list(range(len(cards)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first_owner: dict[str, int] = {}
    for i, card in enumerate(cards):
        for key in [f"tel:{p}" for p in card.phones] + [f"email:{e}" for e in card.emails]:
            if key in first_owner:
                parent[find(i)] = find(first_owner[key])
            else:
                first_owner[key] = i
    groups: dict[int, list[_Card]] = {}
    for i, card in enumerate(cards):
        groups.setdefault(find(i), []).append(card)
    return list(groups.values())


def contact_proposals(engine: Engine, *, min_events: int = 1) -> tuple[ContactProposal, ...]:
    with engine.connect() as connection:
        groups = _group_cards(_load_cards(connection))
        activity = connection.execute(
            text(
                "SELECT source, actor_identity, count(*) FROM events "
                "WHERE actor_identity IS NOT NULL GROUP BY 1, 2"
            )
        ).all()
        linked = {
            (source, value): person_id
            for source, value, person_id in connection.execute(
                text(
                    "SELECT source, identity_value, person_id FROM identity_links "
                    "WHERE lifecycle_state = 'active'"
                )
            )
        }

    by_phone: dict[str, int] = {}
    by_email: dict[str, int] = {}
    by_name: dict[str, set[int]] = {}
    for index, group in enumerate(groups):
        for card in group:
            for p in card.phones:
                by_phone[p] = index
            for e in card.emails:
                by_email[e] = index
            for n in card.names:
                by_name.setdefault(normalize_name(n), set()).add(index)

    matches: dict[int, list[ContactMatch]] = {}
    for source, value, count in activity:
        shape = classify(value)
        index: int | None = None
        kind: MatchKind
        if shape == "phone":
            key = phone_key(value)
            index, kind = (by_phone.get(key) if key else None), "phone"
        elif shape == "email":
            index, kind = by_email.get(normalize_email(value)), "email"
        elif shape == "name":
            candidates = by_name.get(normalize_name(value), set())
            index, kind = (next(iter(candidates)) if len(candidates) == 1 else None), "name"
        else:
            continue
        if index is None:
            continue
        matches.setdefault(index, []).append(
            ContactMatch(
                source=source,
                identity_value=value,
                match=kind,
                events=count,
                linked_person_id=linked.get((source, value)),
            )
        )

    proposals = []
    for index, group_matches in matches.items():
        total = sum(m.events for m in group_matches)
        if total < min_events:
            continue
        group = groups[index]
        names = [n for card in group for n in card.names]
        display_name = max(names, key=lambda n: (names.count(n), len(n))) if names else (
            group_matches[0].identity_value
        )
        alias_forms = names + [card.given for card in group if card.given]
        aliases: dict[str, str] = {}
        for alias in alias_forms:
            aliases.setdefault(normalize_name(alias), alias)
        record_ids = tuple(sorted(card.record_id for card in group))
        proposals.append(
            ContactProposal(
                key=f"contact:{record_ids[0]}",
                display_name=display_name,
                aliases=tuple(aliases.values()),
                card_record_ids=record_ids,
                matches=tuple(sorted(group_matches, key=lambda m: -m.events)),
                total_events=total,
                existing_person_ids=tuple(
                    sorted({m.linked_person_id for m in group_matches if m.linked_person_id})
                ),
            )
        )
    proposals.sort(key=lambda p: (-p.total_events, p.key))
    return tuple(proposals)


def add_alias(engine: Engine, person_id: int, alias: str, *, evidence: dict | None = None) -> bool:
    """Add an active alias; returns False if the person already has it."""
    normalized = normalize_name(alias)
    if not normalized:
        raise ValueError("alias is empty")
    with engine.begin() as connection:
        if connection.execute(
            text("SELECT 1 FROM people WHERE id = :id"), {"id": person_id}
        ).scalar_one_or_none() is None:
            raise ValueError(f"no person with id {person_id}")
        inserted = connection.execute(
            text(
                "INSERT INTO identity.alias (person_id, alias, normalized_alias, evidence) "
                "VALUES (:person_id, :alias, :normalized, :evidence) "
                "ON CONFLICT (person_id, normalized_alias) WHERE lifecycle_state = 'active' "
                "DO NOTHING RETURNING id"
            ),
            {
                "person_id": person_id,
                "alias": alias.strip(),
                "normalized": normalized,
                "evidence": json.dumps(evidence or {}),
            },
        ).first()
    return inserted is not None


def confirm_contact_proposal(
    engine: Engine,
    key: str,
    *,
    person_id: int | None = None,
    exclude: frozenset[tuple[str, str]] = frozenset(),
) -> ConfirmResult:
    """Link a proposal's identities to one person and record the contact's names as aliases.

    The target is `person_id` if given, else the one person the proposal's identities are already
    linked to, else a new person named after the contact. Identities already linked to a
    *different* person are skipped and reported, never re-linked -- that needs an explicit split.
    """
    proposal = next((p for p in contact_proposals(engine) if p.key == key), None)
    if proposal is None:
        raise ValueError(f"no contact proposal {key!r}")

    created = False
    if person_id is None:
        if len(proposal.existing_person_ids) > 1:
            raise ValueError(
                f"{key} matches identities already linked to people {proposal.existing_person_ids};"
                " pass --person-id to choose one"
            )
        if proposal.existing_person_ids:
            person_id = proposal.existing_person_ids[0]
        else:
            person_id = create_person(engine, proposal.display_name).id
            created = True

    evidence = {"via": "contact_card", "card_record_ids": list(proposal.card_record_ids)}
    linked, already, conflicts = [], [], []
    for m in proposal.matches:
        pair = (m.source, m.identity_value)
        if pair in exclude:
            continue
        if m.linked_person_id == person_id:
            already.append(pair)
            continue
        if m.linked_person_id is not None:
            conflicts.append((m.source, m.identity_value, m.linked_person_id))
            continue
        merge_identity(
            engine,
            person_id,
            m.source,
            m.identity_value,
            note=f"confirmed via contact card ({m.match} match)",
            evidence={**evidence, "match": m.match},
        )
        linked.append(pair)

    aliases_added = tuple(
        a for a in proposal.aliases if add_alias(engine, person_id, a, evidence=evidence)
    )
    return ConfirmResult(
        key=key,
        person_id=person_id,
        created_person=created,
        linked=tuple(linked),
        already_linked=tuple(already),
        skipped_conflicts=tuple(conflicts),
        aliases_added=aliases_added,
    )
