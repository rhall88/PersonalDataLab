from __future__ import annotations

import csv
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.imessages.inventory import EXPECTED_HEADERS, inspect_imessages
from personal_data_lab.imessages.parser import iter_imessage_exports

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


def _write(path: Path, *, text_value: str, edited_at: str = "") -> None:
    row = dict.fromkeys(EXPECTED_HEADERS, "")
    row.update(
        {
            "Chat Session": "Stable Chat",
            "Message Date": "2026-09-01 12:00:00",
            "Edited Date": edited_at,
            "Service": "iMessage",
            "Type": "Outgoing",
            "Sender ID": "me@example.test",
            "Sender Name": "Me",
            "Status": "Read",
            "Text": text_value,
        }
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPECTED_HEADERS)
        writer.writeheader()
        writer.writerow(row)


def _exports(path: Path):
    inventory = inspect_imessages(path)
    return tuple(
        iter_imessage_exports(
            inventory.files[0],
            inventory,
            timezone_name="America/Indianapolis",
            device_label="integration-test",
            batch_rows=1,
        )
    )


def test_imessages_exact_replay_and_authoritative_edit(
    database_engine: Engine, tmp_path: Path
) -> None:
    source = tmp_path / "messages.csv"
    _write(source, text_value="before")
    initial = _exports(source)
    first = import_parsed_exports(database_engine, initial)
    replay = import_parsed_exports(database_engine, initial)

    assert sum(result.inserted_events for result in first) == 1
    assert all(result.short_circuited for result in replay)

    _write(source, text_value="after", edited_at="2026-09-01 12:01:00")
    changed = import_parsed_exports(database_engine, _exports(source))
    assert sum(result.updated_events for result in changed) >= 1
    assert sum(result.conflicted_events for result in changed) == 0

    with database_engine.connect() as connection:
        message = connection.execute(
            text(
                """
                SELECT text, canonical_revision, lifecycle_state
                FROM events
                WHERE source = 'imessages'
                  AND source_record_namespace = 'imazing_csv/message'
                """
            )
        ).one()
        versions = int(
            connection.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM event_versions v
                    JOIN events e ON e.id = v.event_id
                    WHERE e.source = 'imessages'
                      AND e.source_record_namespace = 'imazing_csv/message'
                    """
                )
            ).scalar_one()
        )
    assert message.text == "after"
    assert message.canonical_revision == 2
    assert message.lifecycle_state == "active"
    assert versions == 2
