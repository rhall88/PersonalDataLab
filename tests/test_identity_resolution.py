from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text

from personal_data_lab import cli
from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.identity import (
    create_person,
    get_person,
    list_identity_candidates,
    list_people,
    merge_identity,
    split_identity,
)
from personal_data_lab.models import NormalizedEvent, NormalizedRecord, ParsedExport

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
                    relationships, identity_links, identities, people, conversations,
                    source_files, import_runs
                RESTART IDENTITY CASCADE
                """
            )
        )
    yield


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _import(engine: Engine, source: str, filename: str, **kwargs: object) -> None:
    parsed = ParsedExport(
        source=source,
        parser_version="test-identity-v1",
        filename=filename,
        original_path=filename,
        sha256=_sha(filename),
        size_bytes=1,
        **kwargs,
    )
    import_parsed_exports(engine, (parsed,))


def _event(record_id: str, actor_identity: str | None, **kwargs: object) -> NormalizedEvent:
    return NormalizedEvent(
        occurred_at=None,
        source=kwargs.pop("source", "google"),
        event_type="mail_message",
        source_record_namespace="google/mail/event",
        source_record_id=record_id,
        actor_identity=actor_identity,
        text="body text",
        **kwargs,
    )


def _record(record_id: str, actor_identity: str | None, **kwargs: object) -> NormalizedRecord:
    return NormalizedRecord(
        source=kwargs.pop("source", "google"),
        record_type="note",
        source_record_namespace="google/keep/record",
        source_record_id=record_id,
        actor_identity=actor_identity,
        title="a record",
        **kwargs,
    )


def test_list_identity_candidates_sums_counts_across_events_and_records(
    database_engine: Engine,
) -> None:
    _import(database_engine, "google", "e1.json", events=(_event("e1", "jane@example.com"),))
    _import(database_engine, "google", "r1.json", records=(_record("r1", "jane@example.com"),))

    candidates = list_identity_candidates(database_engine)

    assert len(candidates) == 1
    assert candidates[0].source == "google"
    assert candidates[0].identity_value == "jane@example.com"
    assert candidates[0].occurrences == 2
    assert candidates[0].linked_person_id is None


def test_list_identity_candidates_excludes_actively_linked_by_default_and_includes_on_request(
    database_engine: Engine,
) -> None:
    _import(database_engine, "google", "e1.json", events=(_event("e1", "jane@example.com"),))
    person = create_person(database_engine, "Jane Doe")
    merge_identity(database_engine, person.id, "google", "jane@example.com")

    default_candidates = list_identity_candidates(database_engine)
    assert default_candidates == ()

    all_candidates = list_identity_candidates(database_engine, include_linked=True)
    assert len(all_candidates) == 1
    assert all_candidates[0].linked_person_id == person.id
    assert all_candidates[0].linked_display_name == "Jane Doe"


def test_list_identity_candidates_filters_by_source_and_min_count(database_engine: Engine) -> None:
    _import(database_engine, "google", "e1.json", events=(_event("e1", "jane@example.com"),))
    _import(
        database_engine,
        "facebook",
        "e2.json",
        events=(_event("e2", "John Smith", source="facebook"),),
    )

    only_google = list_identity_candidates(database_engine, source="google")
    assert [c.source for c in only_google] == ["google"]

    none_at_threshold = list_identity_candidates(database_engine, min_count=2)
    assert none_at_threshold == ()


def test_create_person_and_list_people_roundtrip(database_engine: Engine) -> None:
    person = create_person(database_engine, "Jane Doe")

    people = list_people(database_engine)

    assert len(people) == 1
    assert people[0].id == person.id
    assert people[0].display_name == "Jane Doe"
    assert people[0].linked_identities == ()


def test_get_person_returns_none_for_unknown_id(database_engine: Engine) -> None:
    assert get_person(database_engine, 999999) is None


def test_merge_identity_backfills_actor_person_id_on_both_events_and_records(
    database_engine: Engine,
) -> None:
    _import(database_engine, "google", "e1.json", events=(_event("e1", "jane@example.com"),))
    _import(database_engine, "google", "r1.json", records=(_record("r1", "jane@example.com"),))
    person = create_person(database_engine, "Jane Doe")

    link = merge_identity(database_engine, person.id, "google", "jane@example.com")

    assert link.lifecycle_state == "active"
    assert link.events_updated == 1
    assert link.records_updated == 1
    with database_engine.connect() as connection:
        event_person_id = connection.execute(
            text("SELECT actor_person_id FROM events WHERE source_record_id = 'e1'")
        ).scalar_one()
        record_person_id = connection.execute(
            text("SELECT actor_person_id FROM records WHERE source_record_id = 'r1'")
        ).scalar_one()
    assert event_person_id == person.id
    assert record_person_id == person.id

    fetched = get_person(database_engine, person.id)
    assert fetched is not None
    assert [li.identity_value for li in fetched.linked_identities] == ["jane@example.com"]


def test_merge_identity_rejects_unknown_person(database_engine: Engine) -> None:
    _import(database_engine, "google", "e1.json", events=(_event("e1", "jane@example.com"),))

    with pytest.raises(ValueError, match="no person"):
        merge_identity(database_engine, 999999, "google", "jane@example.com")


def test_merge_identity_rejects_missing_evidence(database_engine: Engine) -> None:
    person = create_person(database_engine, "Jane Doe")

    with pytest.raises(ValueError, match="no canonical evidence"):
        merge_identity(database_engine, person.id, "google", "nobody@example.com")


def test_merge_identity_rejects_relinking_to_a_different_person_without_split(
    database_engine: Engine,
) -> None:
    _import(database_engine, "google", "e1.json", events=(_event("e1", "jane@example.com"),))
    jane = create_person(database_engine, "Jane Doe")
    someone_else = create_person(database_engine, "Someone Else")
    merge_identity(database_engine, jane.id, "google", "jane@example.com")

    with pytest.raises(ValueError, match="already actively"):
        merge_identity(database_engine, someone_else.id, "google", "jane@example.com")


def test_merge_identity_is_idempotent_and_backfills_newly_imported_evidence(
    database_engine: Engine,
) -> None:
    _import(database_engine, "google", "e1.json", events=(_event("e1", "jane@example.com"),))
    person = create_person(database_engine, "Jane Doe")
    first = merge_identity(database_engine, person.id, "google", "jane@example.com")
    assert first.events_updated == 1

    _import(database_engine, "google", "e2.json", events=(_event("e2", "jane@example.com"),))
    second = merge_identity(database_engine, person.id, "google", "jane@example.com")

    assert second.id == first.id
    assert second.events_updated == 1

    with database_engine.connect() as connection:
        link_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM identity_links WHERE source = 'google' "
                "AND identity_value = 'jane@example.com'"
            )
        ).scalar_one()
    assert link_count == 1


def test_merge_identity_annotates_tombstoned_rows_too(database_engine: Engine) -> None:
    _import(database_engine, "google", "e1.json", events=(_event("e1", "jane@example.com"),))
    _import(
        database_engine,
        "google",
        "e1-deleted.json",
        events=(
            _event(
                "e1",
                "jane@example.com",
                authority_kind="exported_at",
                authority_at=datetime(2026, 1, 1, tzinfo=UTC),
                lifecycle_state="provider_deleted",
            ),
        ),
    )
    person = create_person(database_engine, "Jane Doe")

    link = merge_identity(database_engine, person.id, "google", "jane@example.com")

    assert link.events_updated == 1
    with database_engine.connect() as connection:
        actor_person_id, lifecycle_state = connection.execute(
            text(
                "SELECT actor_person_id, lifecycle_state FROM events "
                "WHERE source_record_id = 'e1'"
            )
        ).one()
    assert actor_person_id == person.id
    assert lifecycle_state == "provider_deleted"


def test_split_identity_reverts_link_nulls_actor_person_id_and_preserves_old_row(
    database_engine: Engine,
) -> None:
    _import(database_engine, "google", "e1.json", events=(_event("e1", "jane@example.com"),))
    person = create_person(database_engine, "Jane Doe")
    merge_identity(database_engine, person.id, "google", "jane@example.com")

    link = split_identity(database_engine, "google", "jane@example.com")

    assert link.lifecycle_state == "reverted"
    assert link.reverted_at is not None
    assert link.events_updated == 1
    with database_engine.connect() as connection:
        actor_person_id = connection.execute(
            text("SELECT actor_person_id FROM events WHERE source_record_id = 'e1'")
        ).scalar_one()
    assert actor_person_id is None

    fetched = get_person(database_engine, person.id)
    assert fetched is not None
    assert fetched.linked_identities == ()


def test_split_identity_rejects_when_no_active_link_exists(database_engine: Engine) -> None:
    with pytest.raises(ValueError, match="no active identity link"):
        split_identity(database_engine, "google", "nobody@example.com")


def test_remerge_after_split_inserts_a_new_row_and_old_one_stays_reverted(
    database_engine: Engine,
) -> None:
    _import(database_engine, "google", "e1.json", events=(_event("e1", "jane@example.com"),))
    person = create_person(database_engine, "Jane Doe")
    first = merge_identity(database_engine, person.id, "google", "jane@example.com")
    split_identity(database_engine, "google", "jane@example.com")

    second = merge_identity(database_engine, person.id, "google", "jane@example.com")

    assert second.id != first.id
    with database_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, lifecycle_state FROM identity_links "
                "WHERE source = 'google' AND identity_value = 'jane@example.com' ORDER BY id"
            )
        ).all()
    assert [row.lifecycle_state for row in rows] == ["reverted", "active"]


def test_cli_identity_candidates_and_people_commands_return_json(database_engine: Engine) -> None:
    _import(database_engine, "google", "e1.json", events=(_event("e1", "jane@example.com"),))
    create_person(database_engine, "Jane Doe")
    database_url = database_engine.url.render_as_string(hide_password=False)

    assert cli.main(["identity-candidates", "--database-url", database_url]) == 0
    assert cli.main(["identity-people", "--database-url", database_url]) == 0


def test_cli_identity_create_person_merge_and_split_commands_succeed(
    database_engine: Engine,
) -> None:
    _import(database_engine, "google", "e1.json", events=(_event("e1", "jane@example.com"),))
    database_url = database_engine.url.render_as_string(hide_password=False)

    assert (
        cli.main(["identity-create-person", "Jane Doe", "--database-url", database_url]) == 0
    )
    person = list_people(database_engine)[0]

    assert (
        cli.main(
            [
                "identity-merge",
                str(person.id),
                "google",
                "jane@example.com",
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    assert (
        cli.main(
            ["identity-split", "google", "jane@example.com", "--database-url", database_url]
        )
        == 0
    )


def test_cli_identity_merge_command_reports_error_and_exit_code_1_on_invalid_input(
    database_engine: Engine,
) -> None:
    database_url = database_engine.url.render_as_string(hide_password=False)

    exit_code = cli.main(
        ["identity-merge", "999999", "google", "nobody@example.com", "--database-url", database_url]
    )

    assert exit_code == 1
