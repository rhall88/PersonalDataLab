"""Exit tests 1-5 from PHASE2_IMPLEMENTATION_PLAN.md (exit test 6 is the real-database rollout
step, done live during PR C, not here; migration 012's own "no canonical table altered" exit
test lives in tests/test_migrations.py::test_migration_010_touches_no_canonical_table, which
already covers every migration through LATEST_SCHEMA_VERSION). Mirrors tests/test_phase1.py's
fixtures and _import_event helper.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text

from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.identity import (
    create_person,
    merge_identity,
    not_same_as_identity,
    split_identity,
)
from personal_data_lab.identity_rules import run_rules_tier
from personal_data_lab.models import NormalizedEvent, ParsedExport

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    test_database_url = os.getenv("TEST_DATABASE_URL")
    if not test_database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_database_engine(test_database_url)
    with engine.connect() as connection:
        database_name = str(connection.execute(text("SELECT current_database()")).scalar_one())
    if not database_name.endswith("_test"):
        engine.dispose()
        pytest.fail("integration tests require a database name ending in '_test'")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(database_engine: Engine) -> Iterator[None]:
    with database_engine.begin() as connection:
        connection.execute(
            text(
                """
                TRUNCATE TABLE
                    relationship_observations, asset_observations, record_observations,
                    event_observations, conversation_observations, attachment_observations,
                    event_versions, conversation_versions, attachment_versions,
                    asset_versions, record_versions, relationship_versions,
                    import_run_source_files, source_file_locations, attachments,
                    source_file_assets, source_file_relationships, source_file_records,
                    source_file_events, source_file_conversations, events, assets, records,
                    relationships, identities, people, conversations, source_files, import_runs
                RESTART IDENTITY CASCADE
                """
            )
        )
        # TRUNCATE ... CASCADE on people already reaches identity_links, identity.assertion, and
        # identity.not_same_as (all FK-reference people), but the standalone
        # identity.resolution_version_seq isn't a table-owned identity sequence, so it needs its
        # own reset between tests.
        connection.execute(text("ALTER SEQUENCE identity.resolution_version_seq RESTART WITH 1"))
    yield


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _import_event(
    engine: Engine,
    source: str,
    filename: str,
    occurred_at: datetime,
    event_id: str,
    *,
    actor_identity: str | None = None,
) -> int:
    parsed = ParsedExport(
        source=source,
        parser_version="test-phase2-v1",
        filename=filename,
        original_path=filename,
        sha256=_sha(filename),
        size_bytes=1,
        events=(
            NormalizedEvent(
                occurred_at=occurred_at,
                source=source,
                event_type="message",
                source_record_namespace=f"{source}/message/event",
                source_record_id=event_id,
                actor_identity=actor_identity,
                text=f"event {event_id}",
            ),
        ),
    )
    return import_parsed_exports(engine, (parsed,))[0].run_id


def _assertion_rows(engine: Engine, source: str, identity_value: str) -> list:
    with engine.connect() as connection:
        return (
            connection.execute(
                text(
                    "SELECT action, tier, resolution_version FROM identity.assertion "
                    "WHERE source = :source AND identity_value = :value ORDER BY id"
                ),
                {"source": source, "value": identity_value},
            )
            .mappings()
            .all()
        )


# --- exit test 1: rules tier links across sources on a shared normalized email/phone ----------


def test_rules_tier_links_across_sources_on_shared_email_and_phone(
    database_engine: Engine,
) -> None:
    when = datetime(2024, 6, 1, tzinfo=UTC)
    _import_event(
        database_engine, "sourcea", "a1.json", when, "a1",
        actor_identity="Friend One <friend@example.com>",
    )
    _import_event(
        database_engine, "sourceb", "b1.json", when, "b1",
        actor_identity=" FRIEND@EXAMPLE.COM ",
    )
    _import_event(
        database_engine, "sourcec", "c1.json", when, "c1", actor_identity="+1 (317) 364-8707"
    )
    _import_event(
        database_engine, "sourced", "d1.json", when, "d1", actor_identity="+13173648707"
    )
    # A role label repeated across two sources must never be treated as a match candidate.
    _import_event(database_engine, "sourcee", "e1.json", when, "e1", actor_identity="you")
    _import_event(database_engine, "sourcef", "f1.json", when, "f1", actor_identity="you")

    outcome = run_rules_tier(database_engine)

    assert outcome.conflicts == ()
    shapes_and_sources = {
        link.shape: {source for source, _ in link.members} for link in outcome.links
    }
    assert shapes_and_sources == {
        "email": {"sourcea", "sourceb"},
        "phone": {"sourcec", "sourced"},
    }
    email_link = next(link for link in outcome.links if link.shape == "email")
    phone_link = next(link for link in outcome.links if link.shape == "phone")
    assert email_link.person_id != phone_link.person_id

    with database_engine.connect() as connection:
        tiers = connection.execute(
            text(
                "SELECT source, tier FROM identity_links WHERE lifecycle_state = 'active' "
                "ORDER BY source"
            )
        ).all()
    assert {row.tier for row in tiers} == {"computed"}
    assert {row.source for row in tiers} == {"sourcea", "sourceb", "sourcec", "sourced"}


# --- exit test 2: resolution_version is monotonic and recorded per assertion -------------------


def test_resolution_version_is_monotonic_across_sequential_operations(
    database_engine: Engine,
) -> None:
    _import_event(
        database_engine, "versrc", "v1.json", datetime(2024, 6, 1, tzinfo=UTC), "v1",
        actor_identity="versioned person",
    )
    person = create_person(database_engine, "Versioned Person")

    merge_identity(database_engine, person.id, "versrc", "versioned person")
    split_identity(database_engine, "versrc", "versioned person")
    merge_identity(database_engine, person.id, "versrc", "versioned person")

    rows = _assertion_rows(database_engine, "versrc", "versioned person")
    assert [row["action"] for row in rows] == ["link", "unlink", "link"]
    versions = [row["resolution_version"] for row in rows]
    assert versions == sorted(versions)
    assert len(set(versions)) == 3


# --- exit test 3: a not_same_as declaration is permanent against the rules tier ----------------


def test_not_same_as_blocks_the_rules_tier(database_engine: Engine) -> None:
    when = datetime(2024, 6, 1, tzinfo=UTC)
    _import_event(
        database_engine, "blocksrc1", "bs1.json", when, "bs1",
        actor_identity="blocked@example.com",
    )
    _import_event(
        database_engine, "blocksrc2", "bs2.json", when, "bs2",
        actor_identity="BLOCKED@example.com",
    )

    not_same_as_identity(database_engine, "blocksrc1", "blocked@example.com")

    outcome = run_rules_tier(database_engine)

    assert outcome.conflicts == ()
    assert outcome.links == ()
    with database_engine.connect() as connection:
        active = connection.execute(
            text(
                "SELECT count(*) FROM identity_links WHERE lifecycle_state = 'active' "
                "AND source IN ('blocksrc1', 'blocksrc2')"
            )
        ).scalar_one()
    assert active == 0


# --- exit test 4: a rules-tier conflict is reported, never silently resolved ------------------


def test_rules_tier_reports_a_conflict_instead_of_picking_a_person(database_engine: Engine) -> None:
    when = datetime(2024, 6, 1, tzinfo=UTC)
    _import_event(
        database_engine, "conflicta", "ca1.json", when, "ca1",
        actor_identity="conflict@example.com",
    )
    _import_event(
        database_engine, "conflictb", "cb1.json", when, "cb1",
        actor_identity="CONFLICT@example.com",
    )
    person_a = create_person(database_engine, "Person A")
    person_b = create_person(database_engine, "Person B")
    merge_identity(database_engine, person_a.id, "conflicta", "conflict@example.com")
    merge_identity(database_engine, person_b.id, "conflictb", "CONFLICT@example.com")

    outcome = run_rules_tier(database_engine)

    assert len(outcome.conflicts) == 1
    conflict = outcome.conflicts[0]
    assert conflict.shape == "email"
    assert set(conflict.person_ids) == {person_a.id, person_b.id}
    assert outcome.links == ()

    # Neither existing link was touched by the conflicting rules-tier pass.
    with database_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT source, person_id FROM identity_links WHERE lifecycle_state = 'active' "
                "AND source IN ('conflicta', 'conflictb') ORDER BY source"
            )
        ).all()
    assert dict(rows) == {"conflicta": person_a.id, "conflictb": person_b.id}


# --- exit test 5: merge/split/merge round-trips cleanly through the assertion log --------------


def test_merge_split_merge_round_trips_through_the_assertion_log(database_engine: Engine) -> None:
    _import_event(
        database_engine, "roundtrip", "rt1.json", datetime(2024, 6, 1, tzinfo=UTC), "rt1",
        actor_identity="round tripper",
    )
    person = create_person(database_engine, "Round Tripper")

    first = merge_identity(database_engine, person.id, "roundtrip", "round tripper")
    assert first.lifecycle_state == "active"

    split = split_identity(database_engine, "roundtrip", "round tripper")
    assert split.lifecycle_state == "reverted"

    second = merge_identity(database_engine, person.id, "roundtrip", "round tripper")
    assert second.lifecycle_state == "active"
    assert second.id != first.id  # a later merge always inserts a new row, never reuses one

    with database_engine.connect() as connection:
        active_count = connection.execute(
            text(
                "SELECT count(*) FROM identity_links WHERE source = 'roundtrip' "
                "AND identity_value = 'round tripper' AND lifecycle_state = 'active'"
            )
        ).scalar_one()

    assert active_count == 1
    rows = _assertion_rows(database_engine, "roundtrip", "round tripper")
    assert [row["action"] for row in rows] == ["link", "unlink", "link"]
