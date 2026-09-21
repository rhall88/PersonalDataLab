from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text

from personal_data_lab import cli
from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.models import (
    NormalizedAsset,
    NormalizedConversation,
    NormalizedEvent,
    NormalizedRecord,
    ParsedExport,
)
from personal_data_lab.search import search_canonical

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
    yield


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _import(engine: Engine, source: str, filename: str, **kwargs: object) -> None:
    parsed = ParsedExport(
        source=source,
        parser_version="test-search-v1",
        filename=filename,
        original_path=filename,
        sha256=_sha(filename),
        size_bytes=1,
        **kwargs,
    )
    import_parsed_exports(engine, (parsed,))


def test_search_finds_matches_across_all_four_kinds(database_engine: Engine) -> None:
    _import(
        database_engine,
        "apple",
        "record.json",
        records=(
            NormalizedRecord(
                source="apple",
                record_type="note",
                source_record_namespace="apple/notes/record",
                source_record_id="n1",
                title="Grocery list",
                text="Buy milk, eggs, and sourdough bread",
            ),
        ),
    )
    _import(
        database_engine,
        "google",
        "event.json",
        events=(
            NormalizedEvent(
                occurred_at=None,
                source="google",
                event_type="mail_message",
                source_record_namespace="google/mail/event",
                source_record_id="e1",
                text="Meeting notes about the sourdough bakery partnership",
            ),
        ),
    )
    _import(
        database_engine,
        "facebook",
        "conversation.json",
        conversations=(
            NormalizedConversation(
                source="facebook",
                source_conversation_id="c1",
                source_record_namespace="facebook/messages/conversation",
                title="Sourdough Bread Club",
            ),
        ),
    )
    _import(
        database_engine,
        "google",
        "asset.json",
        assets=(
            NormalizedAsset(
                source="google",
                asset_namespace="google/photos/asset",
                source_asset_id="a1",
                filename="sourdough_starter.jpg",
                original_path="x",
                size_bytes=1,
            ),
        ),
    )

    results = search_canonical(database_engine, "sourdough")

    assert {hit.kind for hit in results.hits} == {"conversation", "event", "record", "asset"}
    assert len(results.hits) == 4
    for hit in results.hits:
        assert hit.snippet is not None and "<b>" in hit.snippet
        assert hit.canonical_run_id is not None


def test_title_match_outranks_metadata_only_match(database_engine: Engine) -> None:
    _import(
        database_engine,
        "apple",
        "titled.json",
        records=(
            NormalizedRecord(
                source="apple",
                record_type="note",
                source_record_namespace="apple/notes/record",
                source_record_id="titled",
                title="Rhubarb pie",
                text="A family recipe",
            ),
        ),
    )
    _import(
        database_engine,
        "apple",
        "buried.json",
        records=(
            NormalizedRecord(
                source="apple",
                record_type="note",
                source_record_namespace="apple/notes/record",
                source_record_id="buried",
                title="Unrelated title",
                text="Nothing relevant here",
                metadata={"apple": {"tags": ["rhubarb", "garden"]}},
            ),
        ),
    )

    results = search_canonical(database_engine, "rhubarb")

    assert [hit.identifier for hit in results.hits] == ["titled", "buried"]
    assert results.hits[0].rank > results.hits[1].rank


def test_tombstoned_rows_excluded_by_default_and_included_on_request(
    database_engine: Engine,
) -> None:
    _import(
        database_engine,
        "apple",
        "active.json",
        records=(
            NormalizedRecord(
                source="apple",
                record_type="note",
                source_record_namespace="apple/notes/record",
                source_record_id="tombstone-test",
                title="Marmalade",
                text="A jar of marmalade",
            ),
        ),
    )
    _import(
        database_engine,
        "apple",
        "deleted.json",
        records=(
            NormalizedRecord(
                source="apple",
                record_type="note",
                source_record_namespace="apple/notes/record",
                source_record_id="tombstone-test",
                title="Marmalade",
                text="A jar of marmalade",
                authority_kind="exported_at",
                authority_at=datetime(2026, 1, 1, tzinfo=UTC),
                lifecycle_state="provider_deleted",
            ),
        ),
    )

    default_results = search_canonical(database_engine, "marmalade")
    assert len(default_results.hits) == 0

    with_deleted = search_canonical(database_engine, "marmalade", include_deleted=True)
    assert len(with_deleted.hits) == 1
    assert with_deleted.hits[0].lifecycle_state == "provider_deleted"


def test_source_and_kind_filters_narrow_results(database_engine: Engine) -> None:
    _import(
        database_engine,
        "apple",
        "apple_record.json",
        records=(
            NormalizedRecord(
                source="apple",
                record_type="note",
                source_record_namespace="apple/notes/record",
                source_record_id="apple-marzipan",
                title="Marzipan",
                text="Marzipan recipe from Apple Notes",
            ),
        ),
    )
    _import(
        database_engine,
        "google",
        "google_event.json",
        events=(
            NormalizedEvent(
                occurred_at=None,
                source="google",
                event_type="note",
                source_record_namespace="google/keep/event",
                source_record_id="google-marzipan",
                text="Marzipan recipe saved from Google Keep",
            ),
        ),
    )

    only_apple = search_canonical(database_engine, "marzipan", sources=("apple",))
    assert len(only_apple.hits) == 1
    assert only_apple.hits[0].source == "apple"

    only_records = search_canonical(database_engine, "marzipan", kinds=("record",))
    assert len(only_records.hits) == 1
    assert only_records.hits[0].kind == "record"

    both = search_canonical(database_engine, "marzipan")
    assert len(both.hits) == 2


def test_updating_a_record_through_the_normal_import_path_reindexes_it_automatically(
    database_engine: Engine,
) -> None:
    """Proves search.py needed zero changes to any importer: the trigger alone keeps up."""
    _import(
        database_engine,
        "apple",
        "before.json",
        records=(
            NormalizedRecord(
                source="apple",
                record_type="note",
                source_record_namespace="apple/notes/record",
                source_record_id="reindex-test",
                title="Bread notes",
                text="Recipe uses sourdough starter",
            ),
        ),
    )
    assert len(search_canonical(database_engine, "sourdough").hits) == 1

    _import(
        database_engine,
        "apple",
        "after.json",
        records=(
            NormalizedRecord(
                source="apple",
                record_type="note",
                source_record_namespace="apple/notes/record",
                source_record_id="reindex-test",
                title="Bread notes",
                text="Recipe now uses pumpernickel instead",
                authority_kind="exported_at",
                authority_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
    )

    assert len(search_canonical(database_engine, "sourdough").hits) == 0
    assert len(search_canonical(database_engine, "pumpernickel").hits) == 1


def test_cli_search_command_returns_provenance_rich_json(database_engine: Engine) -> None:
    _import(
        database_engine,
        "apple",
        "cli_record.json",
        records=(
            NormalizedRecord(
                source="apple",
                record_type="note",
                source_record_namespace="apple/notes/record",
                source_record_id="cli-test",
                title="Cardamom bun",
                text="Swedish cardamom bun recipe",
            ),
        ),
    )
    database_url = database_engine.url.render_as_string(hide_password=False)

    exit_code = cli.main(["search", "cardamom", "--database-url", database_url])

    assert exit_code == 0
