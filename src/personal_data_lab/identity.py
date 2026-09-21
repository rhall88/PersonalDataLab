from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

LifecycleState = Literal["active", "reverted"]

_CANDIDATES_SQL = """
    WITH counts AS (
        SELECT source, actor_identity AS identity_value, COUNT(*) AS occurrences
        FROM events
        WHERE actor_identity IS NOT NULL
          AND (CAST(:source AS text) IS NULL OR source = CAST(:source AS text))
        GROUP BY source, actor_identity
        UNION ALL
        SELECT source, actor_identity AS identity_value, COUNT(*) AS occurrences
        FROM records
        WHERE actor_identity IS NOT NULL
          AND (CAST(:source AS text) IS NULL OR source = CAST(:source AS text))
        GROUP BY source, actor_identity
    ),
    combined AS (
        SELECT source, identity_value, SUM(occurrences) AS occurrences
        FROM counts
        GROUP BY source, identity_value
    )
    SELECT c.source, c.identity_value, c.occurrences,
           il.person_id AS linked_person_id, p.display_name AS linked_display_name
    FROM combined c
    LEFT JOIN identity_links il
           ON il.source = c.source AND il.identity_value = c.identity_value
          AND il.lifecycle_state = 'active'
    LEFT JOIN people p ON p.id = il.person_id
    WHERE c.occurrences >= :min_count
      AND (:include_linked OR il.id IS NULL)
    ORDER BY c.occurrences DESC, c.source, c.identity_value
"""


class IdentityCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    identity_value: str
    occurrences: int
    linked_person_id: int | None
    linked_display_name: str | None


class LinkedIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    identity_value: str
    note: str | None
    created_at: datetime


class Person(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    display_name: str
    created_at: datetime
    linked_identities: tuple[LinkedIdentity, ...]


class IdentityLink(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    person_id: int
    source: str
    identity_value: str
    lifecycle_state: LifecycleState
    note: str | None
    created_at: datetime
    reverted_at: datetime | None
    events_updated: int
    records_updated: int


def list_identity_candidates(
    engine: Engine,
    *,
    source: str | None = None,
    min_count: int = 1,
    include_linked: bool = False,
) -> tuple[IdentityCandidate, ...]:
    """List distinct (source, actor_identity) values seen across events and records.

    Counts are summed across both tables so a value appearing in both is reported once. By
    default, values that already have an active identity link are omitted so the result is the
    backlog of undecided identities; include_linked=True shows everything.
    """
    with engine.connect() as connection:
        rows = (
            connection.execute(
                text(_CANDIDATES_SQL),
                {"source": source, "min_count": min_count, "include_linked": include_linked},
            )
            .mappings()
            .all()
        )
    return tuple(
        IdentityCandidate(
            source=row["source"],
            identity_value=row["identity_value"],
            occurrences=int(row["occurrences"]),
            linked_person_id=row["linked_person_id"],
            linked_display_name=row["linked_display_name"],
        )
        for row in rows
    )


def create_person(engine: Engine, display_name: str) -> Person:
    with engine.begin() as connection:
        row = (
            connection.execute(
                text(
                    "INSERT INTO people (display_name) VALUES (:display_name) "
                    "RETURNING id, display_name, created_at"
                ),
                {"display_name": display_name},
            )
            .mappings()
            .one()
        )
    return Person(
        id=row["id"],
        display_name=row["display_name"],
        created_at=row["created_at"],
        linked_identities=(),
    )


def _linked_identities(connection, person_id: int) -> tuple[LinkedIdentity, ...]:
    rows = (
        connection.execute(
            text(
                "SELECT source, identity_value, note, created_at FROM identity_links "
                "WHERE person_id = :person_id AND lifecycle_state = 'active' "
                "ORDER BY created_at"
            ),
            {"person_id": person_id},
        )
        .mappings()
        .all()
    )
    return tuple(
        LinkedIdentity(
            source=row["source"],
            identity_value=row["identity_value"],
            note=row["note"],
            created_at=row["created_at"],
        )
        for row in rows
    )


def list_people(engine: Engine) -> tuple[Person, ...]:
    with engine.connect() as connection:
        people_rows = (
            connection.execute(text("SELECT id, display_name, created_at FROM people ORDER BY id"))
            .mappings()
            .all()
        )
        return tuple(
            Person(
                id=row["id"],
                display_name=row["display_name"],
                created_at=row["created_at"],
                linked_identities=_linked_identities(connection, row["id"]),
            )
            for row in people_rows
        )


def get_person(engine: Engine, person_id: int) -> Person | None:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text("SELECT id, display_name, created_at FROM people WHERE id = :id"),
                {"id": person_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return Person(
            id=row["id"],
            display_name=row["display_name"],
            created_at=row["created_at"],
            linked_identities=_linked_identities(connection, row["id"]),
        )


def merge_identity(
    engine: Engine,
    person_id: int,
    source: str,
    identity_value: str,
    *,
    note: str | None = None,
) -> IdentityLink:
    """Link (source, identity_value) to person_id, backfilling actor_person_id everywhere.

    Idempotent for the same person: re-running this after new evidence has been imported does
    not create a duplicate identity_links row, but does re-scan and backfill any events/records
    rows that were not yet annotated. Raises ValueError if the person doesn't exist, if no
    evidence exists for (source, identity_value), or if that value is actively linked to a
    different person (split it first).
    """
    with engine.begin() as connection:
        if (
            connection.execute(
                text("SELECT 1 FROM people WHERE id = :person_id"), {"person_id": person_id}
            ).scalar_one_or_none()
            is None
        ):
            raise ValueError(f"no person with id {person_id}")

        has_evidence = connection.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM events WHERE source = :source AND actor_identity = :value"
                "  UNION ALL"
                "  SELECT 1 FROM records WHERE source = :source AND actor_identity = :value"
                ")"
            ),
            {"source": source, "value": identity_value},
        ).scalar_one()
        if not has_evidence:
            raise ValueError(
                f"no canonical evidence found for source={source!r} "
                f"identity_value={identity_value!r}"
            )

        existing = (
            connection.execute(
                text(
                    "SELECT id, person_id FROM identity_links "
                    "WHERE source = :source AND identity_value = :value "
                    "AND lifecycle_state = 'active'"
                ),
                {"source": source, "value": identity_value},
            )
            .mappings()
            .one_or_none()
        )

        if existing is not None and existing["person_id"] != person_id:
            raise ValueError(
                f"source={source!r} identity_value={identity_value!r} is already actively "
                f"linked to person {existing['person_id']}; split it before merging elsewhere"
            )

        if existing is None:
            try:
                link_id = connection.execute(
                    text(
                        "INSERT INTO identity_links (person_id, source, identity_value, note) "
                        "VALUES (:person_id, :source, :value, :note) RETURNING id"
                    ),
                    {"person_id": person_id, "source": source, "value": identity_value, "note": note},
                ).scalar_one()
            except IntegrityError as error:
                raise ValueError(
                    f"source={source!r} identity_value={identity_value!r} was linked "
                    "concurrently; retry"
                ) from error
        else:
            link_id = existing["id"]
            if note is not None:
                connection.execute(
                    text("UPDATE identity_links SET note = :note WHERE id = :id"),
                    {"note": note, "id": link_id},
                )

        events_updated = connection.execute(
            text(
                "UPDATE events SET actor_person_id = :person_id "
                "WHERE source = :source AND actor_identity = :value "
                "AND actor_person_id IS DISTINCT FROM :person_id"
            ),
            {"person_id": person_id, "source": source, "value": identity_value},
        ).rowcount
        records_updated = connection.execute(
            text(
                "UPDATE records SET actor_person_id = :person_id "
                "WHERE source = :source AND actor_identity = :value "
                "AND actor_person_id IS DISTINCT FROM :person_id"
            ),
            {"person_id": person_id, "source": source, "value": identity_value},
        ).rowcount

        row = connection.execute(
            text(
                "SELECT id, person_id, source, identity_value, lifecycle_state, note, "
                "created_at, reverted_at FROM identity_links WHERE id = :id"
            ),
            {"id": link_id},
        ).mappings().one()

    return IdentityLink(
        id=row["id"],
        person_id=row["person_id"],
        source=row["source"],
        identity_value=row["identity_value"],
        lifecycle_state=row["lifecycle_state"],
        note=row["note"],
        created_at=row["created_at"],
        reverted_at=row["reverted_at"],
        events_updated=events_updated,
        records_updated=records_updated,
    )


def split_identity(engine: Engine, source: str, identity_value: str) -> IdentityLink:
    """Revert the active identity link for (source, identity_value), nulling actor_person_id.

    The reverted row is kept forever for history; a later merge_identity call always inserts a
    new row rather than reusing it. Raises ValueError if there is no active link to split.
    """
    with engine.begin() as connection:
        existing = (
            connection.execute(
                text(
                    "SELECT id, person_id FROM identity_links "
                    "WHERE source = :source AND identity_value = :value "
                    "AND lifecycle_state = 'active'"
                ),
                {"source": source, "value": identity_value},
            )
            .mappings()
            .one_or_none()
        )
        if existing is None:
            raise ValueError(
                f"no active identity link for source={source!r} identity_value={identity_value!r}"
            )

        connection.execute(
            text(
                "UPDATE identity_links SET lifecycle_state = 'reverted', "
                "reverted_at = CURRENT_TIMESTAMP WHERE id = :id"
            ),
            {"id": existing["id"]},
        )
        events_updated = connection.execute(
            text(
                "UPDATE events SET actor_person_id = NULL "
                "WHERE source = :source AND actor_identity = :value "
                "AND actor_person_id = :person_id"
            ),
            {"source": source, "value": identity_value, "person_id": existing["person_id"]},
        ).rowcount
        records_updated = connection.execute(
            text(
                "UPDATE records SET actor_person_id = NULL "
                "WHERE source = :source AND actor_identity = :value "
                "AND actor_person_id = :person_id"
            ),
            {"source": source, "value": identity_value, "person_id": existing["person_id"]},
        ).rowcount

        row = connection.execute(
            text(
                "SELECT id, person_id, source, identity_value, lifecycle_state, note, "
                "created_at, reverted_at FROM identity_links WHERE id = :id"
            ),
            {"id": existing["id"]},
        ).mappings().one()

    return IdentityLink(
        id=row["id"],
        person_id=row["person_id"],
        source=row["source"],
        identity_value=row["identity_value"],
        lifecycle_state=row["lifecycle_state"],
        note=row["note"],
        created_at=row["created_at"],
        reverted_at=row["reverted_at"],
        events_updated=events_updated,
        records_updated=records_updated,
    )
