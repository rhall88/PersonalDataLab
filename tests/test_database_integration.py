from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text

from personal_data_lab.database import (
    ReconciliationError,
    create_database_engine,
    import_openai_export,
    import_parsed_exports,
)
from personal_data_lab.models import NormalizedConversation, NormalizedEvent, ParsedExport
from personal_data_lab.openai_importer import parse_openai_export

pytestmark = pytest.mark.integration

TABLES = (
    "source_files",
    "conversations",
    "events",
    "source_file_conversations",
    "source_file_events",
)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    test_database_url = os.getenv("TEST_DATABASE_URL")
    if not test_database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_database_engine(test_database_url)
    with engine.connect() as connection:
        database_name = str(connection.execute(text("SELECT current_database()"), {}).scalar_one())
    if not database_name.endswith("_test"):
        engine.dispose()
        pytest.fail("integration tests require a database name ending in '_test'")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(database_engine: Engine) -> Iterator[None]:
    _truncate(database_engine)
    yield
    _truncate(database_engine)


def _truncate(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                TRUNCATE TABLE
                    event_observations,
                    conversation_observations,
                    attachment_observations,
                    import_run_source_files,
                    source_file_locations,
                    attachments,
                    source_file_events,
                    source_file_conversations,
                    events,
                    identities,
                    people,
                    conversations,
                    source_files,
                    import_runs
                RESTART IDENTITY CASCADE
                """
            ),
            {},
        )


def _message(node_id: str, message_id: str, parent_id: str, body: str, offset: int) -> dict:
    return {
        "node_id": node_id,
        "parent_id": parent_id,
        "message": {
            "id": message_id,
            "author": {"role": "user" if offset % 2 else "assistant"},
            "create_time": 1_700_000_000 + offset,
            "content": {"content_type": "text", "parts": [body]},
        },
    }


def _write_export(
    directory: Path,
    messages: list[dict],
    *,
    title: str = "Synthetic",
    updated_at: int = 1_700_000_100,
) -> Path:
    directory.mkdir()
    mapping: dict[str, Any] = {"root": {"id": "root", "parent": None, "message": None}}
    for item in messages:
        mapping[item["node_id"]] = {
            "id": item["node_id"],
            "parent": item["parent_id"],
            "message": item["message"],
        }
    payload = [
        {
            "id": "conversation-1",
            "title": title,
            "create_time": 1_700_000_000,
            "update_time": updated_at,
            "mapping": mapping,
        }
    ]
    path = directory / "conversations.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table_name: int(
                connection.execute(text(f"SELECT COUNT(*) FROM {table_name}"), {}).scalar_one()
            )
            for table_name in TABLES
        }


def test_first_import(database_engine: Engine, tmp_path: Path) -> None:
    path = _write_export(
        tmp_path / "export-a",
        [
            _message("node-a", "message-a", "root", "A", 1),
            _message("node-b", "message-b", "node-a", "B", 2),
        ],
    )

    result = import_openai_export(database_engine, parse_openai_export(path))

    assert result.reconciled
    assert result.source_conversations == result.database_conversations == 1
    assert result.source_events == result.database_events == 2
    assert _counts(database_engine) == {
        "source_files": 1,
        "conversations": 1,
        "events": 2,
        "source_file_conversations": 1,
        "source_file_events": 2,
    }


def test_identical_import_twice_is_idempotent(database_engine: Engine, tmp_path: Path) -> None:
    path = _write_export(
        tmp_path / "export-a",
        [
            _message("node-a", "message-a", "root", "A", 1),
            _message("node-b", "message-b", "node-a", "B", 2),
        ],
    )
    parsed = parse_openai_export(path)

    first = import_openai_export(database_engine, parsed)
    with database_engine.connect() as connection:
        canonical_before = connection.execute(
            text(
                """
                SELECT canonical_revision, canonical_updated_at
                FROM events WHERE source_record_id = 'message-a'
                """
            )
        ).one()
    second = import_openai_export(database_engine, parsed)
    with database_engine.connect() as connection:
        canonical_after = connection.execute(
            text(
                """
                SELECT canonical_revision, canonical_updated_at
                FROM events WHERE source_record_id = 'message-a'
                """
            )
        ).one()

    assert first.source_file_id == second.source_file_id
    assert second.reconciled
    assert second.short_circuited
    assert tuple(canonical_before) == tuple(canonical_after)
    assert _counts(database_engine) == {
        "source_files": 1,
        "conversations": 1,
        "events": 2,
        "source_file_conversations": 1,
        "source_file_events": 2,
    }


def test_overlapping_exports_preserve_membership_without_canonical_duplicates(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    export_a = _write_export(
        tmp_path / "export-a",
        [
            _message("node-a", "message-a", "root", "A", 1),
            _message("node-b", "message-b", "node-a", "B", 2),
        ],
    )
    export_b = _write_export(
        tmp_path / "export-b",
        [
            _message("node-a", "message-a", "root", "A", 1),
            _message("node-b", "message-b", "node-a", "B", 2),
            _message("node-c", "message-c", "node-b", "C", 3),
        ],
        updated_at=1_700_000_200,
    )
    parsed_a = parse_openai_export(export_a)
    parsed_b = parse_openai_export(export_b)
    assert parsed_a.sha256 != parsed_b.sha256

    result_a = import_openai_export(database_engine, parsed_a)
    result_b = import_openai_export(database_engine, parsed_b)

    assert result_a.reconciled and result_b.reconciled
    assert result_a.inserted_events == 2
    assert result_b.inserted_events == 1
    assert result_b.unchanged_events == 2
    assert result_b.updated_events == 0
    assert _counts(database_engine) == {
        "source_files": 2,
        "conversations": 1,
        "events": 3,
        "source_file_conversations": 2,
        "source_file_events": 5,
    }
    with database_engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT sfe.source_file_id, e.source_record_id
                FROM source_file_events sfe
                JOIN events e ON e.id = sfe.event_id
                ORDER BY sfe.source_file_id, e.source_record_id
                """
            ),
            {},
        ).all()
    memberships: dict[int, list[str]] = {}
    for source_file_id, source_record_id in rows:
        memberships.setdefault(int(source_file_id), []).append(str(source_record_id))
    assert memberships[result_a.source_file_id] == ["message-a", "message-b"]
    assert memberships[result_b.source_file_id] == ["message-a", "message-b", "message-c"]


def test_later_export_updates_canonical_event_and_keeps_both_memberships(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    export_a = _write_export(
        tmp_path / "export-a",
        [_message("node-a", "message-a", "root", "hello", 1)],
    )
    export_b = _write_export(
        tmp_path / "export-b",
        [_message("node-a", "message-a", "root", "hello edited", 1)],
        updated_at=1_700_000_200,
    )

    import_openai_export(database_engine, parse_openai_export(export_a))
    result = import_openai_export(database_engine, parse_openai_export(export_b))

    assert result.updated_events == 1

    assert _counts(database_engine) == {
        "source_files": 2,
        "conversations": 1,
        "events": 1,
        "source_file_conversations": 2,
        "source_file_events": 2,
    }
    with database_engine.connect() as connection:
        event = connection.execute(
            text(
                """
                SELECT text, source_node_id, parent_source_node_id
                FROM events
                WHERE source = 'openai' AND source_record_id = 'message-a'
                """
            ),
            {},
        ).one()
        observations = connection.execute(
            text(
                """
                SELECT outcome, encode(observed_content_fingerprint, 'hex')
                FROM event_observations eo
                JOIN events e ON e.id = eo.event_id
                WHERE e.source_record_id = 'message-a'
                ORDER BY eo.import_run_id
                """
            )
        ).all()
    assert tuple(event) == ("hello edited", "node-a", "root")
    assert [row[0] for row in observations] == ["inserted", "updated"]
    assert len({row[1] for row in observations}) == 2


def test_older_export_imported_later_cannot_regress_canonical_event(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    newer = _write_export(
        tmp_path / "newer",
        [_message("node-a", "message-a", "root", "current", 1)],
        updated_at=1_700_000_300,
    )
    older = _write_export(
        tmp_path / "older",
        [_message("node-a", "message-a", "root", "stale", 1)],
        updated_at=1_700_000_200,
    )

    import_openai_export(database_engine, parse_openai_export(newer))
    stale = import_openai_export(database_engine, parse_openai_export(older))

    assert stale.stale_events == 1
    assert stale.updated_events == 0
    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT text, canonical_revision
                FROM events
                WHERE source = 'openai'
                  AND source_record_namespace = 'message'
                  AND source_record_id = 'message-a'
                """
            )
        ).one()
    assert tuple(row) == ("current", 1)


def test_later_export_absence_keeps_historical_canonical_event(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    baseline = _write_export(
        tmp_path / "baseline",
        [
            _message("node-a", "message-a", "root", "A", 1),
            _message("node-b", "message-b", "root", "B", 2),
        ],
        updated_at=1_700_000_100,
    )
    later = _write_export(
        tmp_path / "later",
        [_message("node-b", "message-b", "root", "B", 2)],
        updated_at=1_700_000_200,
    )

    import_openai_export(database_engine, parse_openai_export(baseline))
    import_openai_export(database_engine, parse_openai_export(later))

    with database_engine.connect() as connection:
        record_ids = connection.execute(
            text(
                """
                SELECT source_record_id
                FROM events
                WHERE source = 'openai' AND source_record_namespace = 'message'
                ORDER BY source_record_id
                """
            )
        ).scalars().all()
    assert list(record_ids) == ["message-a", "message-b"]


def test_equal_authority_with_different_content_rolls_back(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    first = _write_export(
        tmp_path / "first",
        [_message("node-a", "message-a", "root", "first", 1)],
        updated_at=1_700_000_100,
    )
    conflicting = _write_export(
        tmp_path / "conflicting",
        [_message("node-a", "message-a", "root", "conflict", 1)],
        updated_at=1_700_000_100,
    )
    import_openai_export(database_engine, parse_openai_export(first))

    with pytest.raises(ReconciliationError, match="equal authority"):
        import_openai_export(database_engine, parse_openai_export(conflicting))

    with database_engine.connect() as connection:
        text_value = connection.execute(
            text("SELECT text FROM events WHERE source_record_id = 'message-a'")
        ).scalar_one()
    assert text_value == "first"


def test_event_namespaces_allow_provider_ids_to_repeat(
    database_engine: Engine,
) -> None:
    parsed = ParsedExport(
        source="synthetic",
        parser_version="synthetic-v1",
        filename="synthetic.json",
        original_path="synthetic://namespaces",
        sha256="f" * 64,
        size_bytes=1,
        events=(
            NormalizedEvent(
                occurred_at=None,
                source="synthetic",
                event_type="first",
                source_record_namespace="first_kind",
                source_record_id="same-id",
            ),
            NormalizedEvent(
                occurred_at=None,
                source="synthetic",
                event_type="second",
                source_record_namespace="second_kind",
                source_record_id="same-id",
            ),
        ),
    )

    result = import_parsed_exports(database_engine, (parsed,))[0]

    assert result.inserted_events == 2
    with database_engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM events")).scalar_one() == 2


def test_parent_node_relationship_resolves_within_conversation(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    path = _write_export(
        tmp_path / "export-a",
        [
            _message("node-user", "message-user", "root", "parent", 1),
            _message("node-assistant", "message-assistant", "node-user", "child", 2),
        ],
    )
    import_openai_export(database_engine, parse_openai_export(path))

    with database_engine.connect() as connection:
        relationship = connection.execute(
            text(
                """
                SELECT child.source_record_id, parent.source_record_id
                FROM events child
                JOIN events parent
                  ON parent.conversation_id = child.conversation_id
                 AND parent.source_node_id = child.parent_source_node_id
                WHERE child.source_record_id = 'message-assistant'
                """
            ),
            {},
        ).one()

    assert tuple(relationship) == ("message-assistant", "message-user")


def test_reconciliation_failure_rolls_back_every_table(database_engine: Engine) -> None:
    duplicate_events = tuple(
        NormalizedEvent(
            occurred_at=datetime(2026, 1, index, tzinfo=UTC),
            source="openai",
            event_type="conversation_message",
            text=f"duplicate {index}",
            source_record_id="duplicate-message",
            source_node_id=f"node-{index}",
            parent_source_node_id="root",
        )
        for index in (1, 2)
    )
    parsed = ParsedExport(
        source="openai",
        parser_version="openai-test-v1",
        filename="synthetic.json",
        original_path="synthetic://rollback-test",
        sha256="a" * 64,
        size_bytes=1,
        conversations=(
            NormalizedConversation(
                source="openai",
                source_conversation_id="rollback-conversation",
                events=duplicate_events,
            ),
        ),
    )

    with pytest.raises(ReconciliationError, match="import rolled back"):
        import_openai_export(database_engine, parsed)

    assert _counts(database_engine) == {table_name: 0 for table_name in TABLES}


def test_batch_import_supports_conversations_and_standalone_events(
    database_engine: Engine,
) -> None:
    message = NormalizedEvent(
        occurred_at=datetime(2026, 9, 4, tzinfo=UTC),
        source="anthropic",
        event_type="conversation_message",
        actor_identity="human",
        text="Synthetic message",
        source_record_id="anthropic-message-1",
        source_node_id="anthropic-message-1",
    )
    conversations_export = ParsedExport(
        source="anthropic",
        parser_version="anthropic-test-v1",
        filename="conversations-000.zip",
        original_path="synthetic://conversations-000.zip",
        sha256="b" * 64,
        size_bytes=1,
        conversations=(
            NormalizedConversation(
                source="anthropic",
                source_conversation_id="anthropic-conversation-1",
                events=(message,),
            ),
        ),
    )
    metadata_export = ParsedExport(
        source="anthropic",
        parser_version="anthropic-test-v1",
        filename="light_metadata-000.zip",
        original_path="synthetic://light_metadata-000.zip",
        sha256="c" * 64,
        size_bytes=1,
        events=(
            NormalizedEvent(
                occurred_at=datetime(2026, 9, 5, tzinfo=UTC),
                source="anthropic",
                event_type="account_login",
                source_record_id="login:synthetic",
            ),
        ),
    )

    results = import_parsed_exports(
        database_engine,
        (conversations_export, metadata_export),
    )

    assert all(result.reconciled for result in results)
    assert _counts(database_engine) == {
        "source_files": 2,
        "conversations": 1,
        "events": 2,
        "source_file_conversations": 1,
        "source_file_events": 2,
    }
    with database_engine.connect() as connection:
        event_types = connection.execute(
            text("SELECT event_type FROM events ORDER BY event_type"),
            {},
        ).scalars().all()
    assert list(event_types) == ["account_login", "conversation_message"]
