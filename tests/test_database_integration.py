from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest
from sqlalchemy import Engine, text

from personal_data_lab import cli
from personal_data_lab.anthropic_importer import _parse_standard_message
from personal_data_lab.database import (
    ReconciliationError,
    create_database_engine,
    import_openai_export,
    import_parsed_exports,
)
from personal_data_lab.google_importer import GoogleExportError, parse_google_export
from personal_data_lab.models import (
    NormalizedAsset,
    NormalizedConversation,
    NormalizedEvent,
    NormalizedRecord,
    NormalizedRelationship,
    ParsedExport,
)
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
                    relationship_observations,
                    asset_observations,
                    record_observations,
                    event_observations,
                    conversation_observations,
                    attachment_observations,
                    event_versions,
                    conversation_versions,
                    attachment_versions,
                    asset_versions,
                    record_versions,
                    relationship_versions,
                    import_run_source_files,
                    source_file_locations,
                    attachments,
                    source_file_assets,
                    source_file_relationships,
                    source_file_records,
                    source_file_events,
                    source_file_conversations,
                    events,
                    assets,
                    records,
                    relationships,
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


def _synthetic_export(
    *,
    sha256: str,
    original_path: str,
    exported_at: datetime | None,
    events: tuple[NormalizedEvent, ...],
    filename: str = "records.json",
    snapshot_kind: str = "complete",
) -> ParsedExport:
    return ParsedExport(
        source="synthetic",
        parser_version="synthetic-v1",
        filename=filename,
        original_path=original_path,
        sha256=sha256,
        size_bytes=1,
        exported_at=exported_at,
        snapshot_kind=snapshot_kind,
        snapshot_scope="account",
        events=events,
    )


def _synthetic_event(
    record_id: str,
    text_value: str | None,
    *,
    lifecycle_state: str = "active",
    authority_at: datetime | None = None,
    authority_kind: str = "unknown",
) -> NormalizedEvent:
    return NormalizedEvent(
        occurred_at=datetime(2025, 1, 1, tzinfo=UTC),
        source="synthetic",
        event_type="row",
        source_record_namespace="row",
        source_record_id=record_id,
        text=text_value,
        lifecycle_state=lifecycle_state,
        authority_at=authority_at,
        authority_kind=authority_kind,
    )


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
                SELECT eo.outcome, encode(ev.content_fingerprint, 'hex')
                FROM event_observations eo
                JOIN event_versions ev ON ev.id = eo.event_version_id
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


def test_equal_authority_conflict_preserves_version_and_canonical(
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

    result = import_openai_export(database_engine, parse_openai_export(conflicting))

    with database_engine.connect() as connection:
        text_value = connection.execute(
            text("SELECT text FROM events WHERE source_record_id = 'message-a'")
        ).scalar_one()
        history = connection.execute(
            text(
                """
                SELECT ev.normalized_payload ->> 'text', eo.outcome
                FROM event_versions ev
                LEFT JOIN event_observations eo ON eo.event_version_id = ev.id
                JOIN events e ON e.id = ev.event_id
                WHERE e.source_record_id = 'message-a'
                ORDER BY ev.id
                """
            )
        ).all()
        run_status = connection.execute(
            text("SELECT status FROM import_runs WHERE id = :run_id"),
            {"run_id": result.run_id},
        ).scalar_one()
    assert text_value == "first"
    assert result.conflicted_events == 1
    assert list(history) == [("first", "inserted"), ("conflict", "conflicted")]
    assert run_status == "conflicted"


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


def test_lossless_100_to_75_snapshot_retains_absent_records(
    database_engine: Engine,
) -> None:
    run_1 = _synthetic_export(
        sha256="1" * 64,
        original_path="synthetic://2025/example.xlsx",
        exported_at=datetime(2025, 1, 1, tzinfo=UTC),
        events=tuple(_synthetic_event(f"row-{index:03}", f"value-{index}") for index in range(100)),
    )
    run_2 = _synthetic_export(
        sha256="2" * 64,
        original_path="synthetic://2027/example.xlsx",
        exported_at=datetime(2027, 1, 1, tzinfo=UTC),
        events=tuple(_synthetic_event(f"row-{index:03}", f"value-{index}") for index in range(75)),
    )

    first = import_parsed_exports(database_engine, (run_1,))[0]
    second = import_parsed_exports(database_engine, (run_2,))[0]

    with database_engine.connect() as connection:
        canonical_count = connection.execute(text("SELECT COUNT(*) FROM events")).scalar_one()
        version_count = connection.execute(text("SELECT COUNT(*) FROM event_versions")).scalar_one()
        memberships = dict(
            connection.execute(
                text(
                    """
                    SELECT import_run_id, COUNT(*)
                    FROM event_observations
                    GROUP BY import_run_id
                    """
                )
            ).all()
        )
        pointer_counts = dict(
            connection.execute(
                text(
                    """
                    SELECT last_observed_run_id, COUNT(*)
                    FROM events
                    GROUP BY last_observed_run_id
                    """
                )
            ).all()
        )
        missing_state = connection.execute(
            text(
                """
                SELECT MIN(canonical_revision), MAX(canonical_revision),
                       COUNT(*) FILTER (WHERE lifecycle_state = 'active')
                FROM events
                WHERE source_record_id >= 'row-075'
                """
            )
        ).one()

    assert canonical_count == version_count == 100
    assert memberships == {first.run_id: 100, second.run_id: 75}
    assert pointer_counts == {first.run_id: 25, second.run_id: 75}
    assert tuple(missing_state) == (1, 1, 25)
    assert second.reused_event_versions == 75


def test_same_content_newer_authority_blocks_later_stale_change(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    first_path = _write_export(
        tmp_path / "first", [_message("node-a", "message-a", "root", "A", 1)],
        updated_at=1_700_000_100,
    )
    confirmation_path = _write_export(
        tmp_path / "confirmation", [_message("node-a", "message-a", "root", "A", 1)],
        updated_at=1_700_000_300,
    )
    stale_path = _write_export(
        tmp_path / "stale", [_message("node-a", "message-a", "root", "B", 1)],
        updated_at=1_700_000_200,
    )

    import_openai_export(database_engine, parse_openai_export(first_path))
    with database_engine.connect() as connection:
        initial_canonical_updated_at = connection.execute(
            text("SELECT canonical_updated_at FROM events WHERE source_record_id = 'message-a'")
        ).scalar_one()
    confirmation = import_openai_export(
        database_engine, parse_openai_export(confirmation_path)
    )
    stale = import_openai_export(database_engine, parse_openai_export(stale_path))

    with database_engine.connect() as connection:
        canonical = connection.execute(
            text(
                """
                SELECT text, canonical_revision, extract(epoch FROM authority_at)::bigint,
                       canonical_run_id, last_observed_run_id, canonical_updated_at
                FROM events WHERE source_record_id = 'message-a'
                """
            )
        ).one()
        version_count = connection.execute(text("SELECT COUNT(*) FROM event_versions")).scalar_one()
        history = connection.execute(
            text("SELECT normalized_payload ->> 'text' FROM event_versions ORDER BY id")
        ).scalars().all()
    assert confirmation.unchanged_events == 1
    assert stale.stale_events == 1
    assert tuple(canonical) == (
        "A", 1, 1_700_000_300, confirmation.run_id, stale.run_id,
        initial_canonical_updated_at,
    )
    assert version_count == 2
    assert list(history) == ["A", "B"]


def test_anthropic_timestamp_only_observation_reuses_version(
    database_engine: Engine,
) -> None:
    first_event = _parse_standard_message(
        "conversation-1",
        {
            "uuid": "message-1",
            "sender": "human",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "content": [{"type": "text", "text": "same content"}],
        },
        0,
        None,
    )
    observed_later = _parse_standard_message(
        "conversation-1",
        {
            "uuid": "message-1",
            "sender": "human",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-02-02T00:00:00Z",
            "content": [{"type": "text", "text": "same content"}],
        },
        0,
        None,
    )

    def parsed_export(sha256: str, event: NormalizedEvent) -> ParsedExport:
        return ParsedExport(
            source="anthropic",
            parser_version="anthropic-v1",
            filename=f"{sha256[0]}.zip",
            original_path=f"synthetic://{sha256[0]}.zip",
            sha256=sha256,
            size_bytes=1,
            snapshot_kind="partial",
            snapshot_scope="account",
            conversations=(
                NormalizedConversation(
                    source="anthropic",
                    source_conversation_id="conversation-1",
                    source_record_namespace="chat",
                    title="Stable conversation",
                    events=(event,),
                ),
            ),
        )

    first = import_parsed_exports(database_engine, (parsed_export("1" * 64, first_event),))[0]
    with database_engine.connect() as connection:
        initial_canonical_updated_at = connection.execute(
            text("SELECT canonical_updated_at FROM events WHERE source_record_id = 'message-1'")
        ).scalar_one()
    second = import_parsed_exports(
        database_engine, (parsed_export("2" * 64, observed_later),)
    )[0]

    with database_engine.connect() as connection:
        canonical = connection.execute(
            text(
                """
                SELECT canonical_revision, authority_at, canonical_run_id,
                       last_observed_run_id, canonical_updated_at
                FROM events WHERE source_record_id = 'message-1'
                """
            )
        ).one()
        version_count = connection.execute(text("SELECT COUNT(*) FROM event_versions")).scalar_one()
        observations = connection.execute(
            text(
                """
                SELECT COUNT(*), COUNT(DISTINCT event_version_id)
                FROM event_observations
                """
            )
        ).one()

    assert first.inserted_events == 1
    assert second.unchanged_events == 1
    assert second.new_event_versions == 0
    assert second.reused_event_versions == 1
    assert tuple(canonical) == (
        1,
        observed_later.authority_at,
        second.run_id,
        second.run_id,
        initial_canonical_updated_at,
    )
    assert version_count == 1
    assert tuple(observations) == (2, 1)


def test_conversation_change_preserves_both_normalized_versions(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    first_path = _write_export(
        tmp_path / "first",
        [_message("node-a", "message-a", "root", "same message", 1)],
        title="Old title",
        updated_at=1_700_000_100,
    )
    second_path = _write_export(
        tmp_path / "second",
        [_message("node-a", "message-a", "root", "same message", 1)],
        title="New title",
        updated_at=1_700_000_200,
    )

    import_openai_export(database_engine, parse_openai_export(first_path))
    result = import_openai_export(database_engine, parse_openai_export(second_path))

    with database_engine.connect() as connection:
        title = connection.execute(text("SELECT title FROM conversations")).scalar_one()
        history = connection.execute(
            text("SELECT normalized_payload ->> 'title' FROM conversation_versions ORDER BY id")
        ).scalars().all()
    assert result.updated_conversations == 1
    assert result.new_conversation_versions == 1
    assert title == "New title"
    assert list(history) == ["Old title", "New title"]


def test_incomparable_authority_preserves_conflict(
    database_engine: Engine,
) -> None:
    first = _synthetic_export(
        sha256="3" * 64,
        original_path="synthetic://first",
        exported_at=None,
        snapshot_kind="partial",
        events=(
            _synthetic_event(
                "row-1", "provider value",
                authority_at=datetime(2025, 1, 1, tzinfo=UTC),
                authority_kind="provider_updated_at",
            ),
        ),
    )
    second = _synthetic_export(
        sha256="4" * 64,
        original_path="synthetic://second",
        exported_at=None,
        snapshot_kind="partial",
        events=(
            _synthetic_event(
                "row-1", "snapshot value",
                authority_at=datetime(2026, 1, 1, tzinfo=UTC),
                authority_kind="exported_at",
            ),
        ),
    )

    import_parsed_exports(database_engine, (first,))
    result = import_parsed_exports(database_engine, (second,))[0]

    with database_engine.connect() as connection:
        canonical = connection.execute(
            text("SELECT text, canonical_revision FROM events")
        ).one()
        versions = connection.execute(text("SELECT COUNT(*) FROM event_versions")).scalar_one()
    assert result.conflicted_events == 1
    assert tuple(canonical) == ("provider value", 1)
    assert versions == 2


def test_tombstone_and_reactivation_preserve_all_versions(
    database_engine: Engine,
) -> None:
    active = _synthetic_export(
        sha256="5" * 64,
        original_path="synthetic://active",
        exported_at=datetime(2025, 1, 1, tzinfo=UTC),
        events=(_synthetic_event("row-1", "value"),),
    )
    deleted = _synthetic_export(
        sha256="6" * 64,
        original_path="synthetic://deleted",
        exported_at=datetime(2026, 1, 1, tzinfo=UTC),
        events=(_synthetic_event("row-1", "value", lifecycle_state="provider_deleted"),),
    )
    restored = _synthetic_export(
        sha256="7" * 64,
        original_path="synthetic://restored",
        exported_at=datetime(2027, 1, 1, tzinfo=UTC),
        events=(_synthetic_event("row-1", "value restored"),),
    )

    import_parsed_exports(database_engine, (active,))
    deletion = import_parsed_exports(database_engine, (deleted,))[0]
    restoration = import_parsed_exports(database_engine, (restored,))[0]

    with database_engine.connect() as connection:
        canonical = connection.execute(
            text("SELECT lifecycle_state, text, canonical_revision FROM events")
        ).one()
        history = connection.execute(
            text(
                """
                SELECT normalized_payload ->> 'lifecycle_state'
                FROM event_versions ORDER BY id
                """
            )
        ).scalars().all()
    assert deletion.tombstoned_events == 1
    assert restoration.updated_events == 1
    assert tuple(canonical) == ("active", "value restored", 3)
    assert list(history) == ["active", "provider_deleted", "active"]


def test_raw_source_identity_uses_sha_not_path(database_engine: Engine) -> None:
    timestamp = datetime(2025, 1, 1, tzinfo=UTC)
    first = _synthetic_export(
        sha256="8" * 64,
        original_path="synthetic://path-a/records.json",
        exported_at=timestamp,
        events=(_synthetic_event("row-1", "value"),),
    )
    same_bytes_new_path = first.model_copy(
        update={
            "filename": "renamed.json",
            "original_path": "synthetic://path-b/renamed.json",
        }
    )
    different_bytes_same_path = first.model_copy(
        update={"sha256": "9" * 64, "exported_at": datetime(2026, 1, 1, tzinfo=UTC)}
    )

    initial = import_parsed_exports(database_engine, (first,))[0]
    duplicate = import_parsed_exports(database_engine, (same_bytes_new_path,))[0]
    changed_file = import_parsed_exports(database_engine, (different_bytes_same_path,))[0]

    with database_engine.connect() as connection:
        source_files = connection.execute(text("SELECT COUNT(*) FROM source_files")).scalar_one()
        locations = connection.execute(
            text("SELECT COUNT(*) FROM source_file_locations")
        ).scalar_one()
    assert duplicate.source_file_id == initial.source_file_id
    assert duplicate.short_circuited
    assert changed_file.source_file_id != initial.source_file_id
    assert source_files == 2
    assert locations == 3


def test_multifile_run_key_is_order_independent(database_engine: Engine) -> None:
    timestamp = datetime(2025, 1, 1, tzinfo=UTC)
    file_a = _synthetic_export(
        sha256="a" * 64,
        original_path="synthetic://a",
        exported_at=timestamp,
        filename="a.json",
        events=(_synthetic_event("row-a", "A"),),
    )
    file_b = _synthetic_export(
        sha256="b" * 64,
        original_path="synthetic://b",
        exported_at=timestamp,
        filename="b.json",
        events=(_synthetic_event("row-b", "B"),),
    )

    forward = import_parsed_exports(database_engine, (file_a, file_b))
    reverse = import_parsed_exports(database_engine, (file_b, file_a))

    assert reverse[0].source_file_id == forward[1].source_file_id
    assert reverse[1].source_file_id == forward[0].source_file_id
    assert all(result.short_circuited for result in reverse)
    with database_engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM import_runs")).scalar_one() == 1


def test_newer_missing_field_keeps_older_payload_queryable(
    database_engine: Engine,
) -> None:
    older = _synthetic_export(
        sha256="c" * 64,
        original_path="synthetic://older",
        exported_at=datetime(2025, 1, 1, tzinfo=UTC),
        events=(_synthetic_event("row-1", "previously populated"),),
    )
    newer = _synthetic_export(
        sha256="d" * 64,
        original_path="synthetic://newer",
        exported_at=datetime(2026, 1, 1, tzinfo=UTC),
        events=(_synthetic_event("row-1", None),),
    )

    import_parsed_exports(database_engine, (older,))
    import_parsed_exports(database_engine, (newer,))

    with database_engine.connect() as connection:
        canonical = connection.execute(text("SELECT text FROM events")).scalar_one()
        history = connection.execute(
            text("SELECT normalized_payload ->> 'text' FROM event_versions ORDER BY id")
        ).scalars().all()
    assert canonical is None
    assert list(history) == ["previously populated", None]


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


def test_event_fingerprint_version_mismatch_rolls_back_import(
    database_engine: Engine,
) -> None:
    initial = _synthetic_export(
        sha256="8" * 64,
        original_path="synthetic://initial",
        exported_at=datetime(2025, 1, 1, tzinfo=UTC),
        events=(_synthetic_event("row-1", "value"),),
    )
    incoming = _synthetic_export(
        sha256="9" * 64,
        original_path="synthetic://incoming",
        exported_at=datetime(2026, 1, 1, tzinfo=UTC),
        events=(_synthetic_event("row-1", "value"),),
    )
    import_parsed_exports(database_engine, (initial,))
    with database_engine.begin() as connection:
        connection.execute(text("UPDATE events SET fingerprint_version = 'canonical-v0'"))
    tables = (
        "source_files",
        "source_file_locations",
        "import_runs",
        "import_run_source_files",
        "events",
        "event_versions",
        "event_observations",
    )
    with database_engine.connect() as connection:
        before = {
            table: connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in tables
        }

    with pytest.raises(ReconciliationError, match="database fingerprints use canonical-v0"):
        import_parsed_exports(database_engine, (incoming,))

    with database_engine.connect() as connection:
        after = {
            table: connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in tables
        }
        stored_version = connection.execute(
            text("SELECT fingerprint_version FROM events")
        ).scalar_one()
    assert after == before
    assert stored_version == "canonical-v0"


def test_conversation_fingerprint_version_mismatch_rolls_back_import(
    database_engine: Engine,
) -> None:
    def parsed_export(sha256: str, title: str) -> ParsedExport:
        return ParsedExport(
            source="synthetic",
            parser_version="synthetic-v1",
            filename=f"{sha256[0]}.json",
            original_path=f"synthetic://{sha256[0]}.json",
            sha256=sha256,
            size_bytes=1,
            exported_at=datetime(2026, 1, 1, tzinfo=UTC),
            snapshot_kind="complete",
            snapshot_scope="account",
            conversations=(
                NormalizedConversation(
                    source="synthetic",
                    source_conversation_id="conversation-1",
                    source_record_namespace="chat",
                    title=title,
                ),
            ),
        )

    import_parsed_exports(database_engine, (parsed_export("a" * 64, "title"),))
    with database_engine.begin() as connection:
        connection.execute(
            text("UPDATE conversations SET fingerprint_version = 'canonical-v0'")
        )
    with database_engine.connect() as connection:
        before = connection.execute(text("SELECT COUNT(*) FROM import_runs")).scalar_one()

    with pytest.raises(ReconciliationError, match="database fingerprints use canonical-v0"):
        import_parsed_exports(database_engine, (parsed_export("b" * 64, "title"),))

    with database_engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM import_runs")).scalar_one() == before
        assert connection.execute(
            text("SELECT fingerprint_version FROM conversations")
        ).scalar_one() == "canonical-v0"


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


def test_google_activity_reexport_reuses_existing_events(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    original = {
        "header": "Search",
        "title": "Searched for streaming JSON",
        "time": "2026-09-01T00:00:00Z",
        "products": ["Search"],
    }
    added = {
        "header": "Maps",
        "title": "Searched for coffee",
        "time": "2026-09-02T00:00:00Z",
        "products": ["Maps"],
    }
    first_path = tmp_path / "first" / "MyActivity.json"
    second_path = tmp_path / "second" / "MyActivity.json"
    first_path.parent.mkdir()
    second_path.parent.mkdir()
    first_path.write_text(json.dumps([original]), encoding="utf-8")
    second_path.write_text(json.dumps([original, added]), encoding="utf-8")

    first = import_parsed_exports(database_engine, parse_google_export(first_path))[0]
    second = import_parsed_exports(database_engine, parse_google_export(second_path))[0]

    assert first.inserted_events == 1
    assert second.unchanged_events == 1
    assert second.inserted_events == 1
    with database_engine.connect() as connection:
        event_types = connection.execute(
            text("SELECT event_type FROM events ORDER BY event_type")
        ).scalars().all()
        versions = connection.execute(text("SELECT COUNT(*) FROM event_versions")).scalar_one()
    assert list(event_types) == ["maps_activity", "search_activity"]
    assert versions == 2


def test_google_export_authority_reuses_version_and_advances_watermark(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    payload = json.dumps(
        [
            {
                "header": "Search",
                "title": "Searched for deterministic ingestion",
                "time": "2026-09-01T00:00:00Z",
            }
        ]
    )
    member = "Takeout/My Activity/Search/MyActivity.json"
    paths = (
        tmp_path / "takeout-20260901T000000Z-001.zip",
        tmp_path / "takeout-20260902T000000Z-001.zip",
    )
    for path in paths:
        with ZipFile(path, "w") as archive:
            archive.writestr(member, payload)

    first = import_parsed_exports(database_engine, parse_google_export(paths[0]))[0]
    second = import_parsed_exports(database_engine, parse_google_export(paths[1]))[0]

    assert first.inserted_events == 1
    assert second.unchanged_events == 1
    assert second.reused_event_versions == 1
    with database_engine.connect() as connection:
        version_count = connection.execute(
            text("SELECT COUNT(*) FROM event_versions")
        ).scalar_one()
        authority_at = connection.execute(
            text("SELECT authority_at FROM events")
        ).scalar_one()
        run_count = connection.execute(text("SELECT COUNT(*) FROM import_runs")).scalar_one()
    assert version_count == 1
    assert authority_at == datetime(2026, 9, 2, tzinfo=UTC)
    assert run_count == 2


def test_google_parsing_category_prevents_false_run_short_circuit(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    source = tmp_path / "MyActivity.json"
    source.write_text(
        json.dumps([{"title": "Activity", "time": "2026-09-01T00:00:00Z"}]),
        encoding="utf-8",
    )
    (generic,) = parse_google_export(source)
    search_context = generic.model_copy(
        update={
            "category": "my_activity/search/json",
            "filename": "My_Activity.json",
            "original_path": str(tmp_path / "Search" / "My_Activity.json"),
        }
    )

    first = import_parsed_exports(database_engine, (generic,))[0]
    second = import_parsed_exports(database_engine, (search_context,))[0]

    assert first.source_file_id == second.source_file_id
    assert not second.short_circuited
    assert second.unchanged_events == 1
    with database_engine.connect() as connection:
        run_count = connection.execute(text("SELECT COUNT(*) FROM import_runs")).scalar_one()
        categories = connection.execute(
            text("SELECT category FROM import_run_source_files ORDER BY import_run_id")
        ).scalars().all()
    assert run_count == 2
    assert list(categories) == [
        "my_activity/generic/json",
        "my_activity/search/json",
    ]


def test_google_exact_file_and_root_imports_share_one_run_and_version(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    source = tmp_path / "Takeout" / "My Activity" / "Search" / "MyActivity.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps([{"title": "Sparse activity"}]), encoding="utf-8")

    first = import_parsed_exports(database_engine, parse_google_export(source))[0]
    second = import_parsed_exports(database_engine, parse_google_export(tmp_path))[0]

    assert first.inserted_events == 1
    assert second.short_circuited
    assert second.source_file_id == first.source_file_id
    with database_engine.connect() as connection:
        event = connection.execute(
            text("SELECT event_type, canonical_revision FROM events")
        ).one()
        version_count = connection.execute(
            text("SELECT COUNT(*) FROM event_versions")
        ).scalar_one()
        run_count = connection.execute(text("SELECT COUNT(*) FROM import_runs")).scalar_one()
    assert tuple(event) == ("search_activity", 1)
    assert version_count == 1
    assert run_count == 1


def test_google_sparse_records_in_different_products_are_distinct_events(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    source = tmp_path / "takeout-20260909T012526Z-001.zip"
    payload = json.dumps([{"title": "Activity"}])
    with ZipFile(source, "w") as archive:
        archive.writestr("Takeout/My Activity/Search/MyActivity.json", payload)
        archive.writestr("Takeout/My Activity/Maps/MyActivity.json", payload)

    parsed_exports = parse_google_export(source)
    results = tuple(
        import_parsed_exports(database_engine, (parsed,))[0]
        for parsed in parsed_exports
    )

    assert sum(result.conflicted_events for result in results) == 0
    with database_engine.connect() as connection:
        events = connection.execute(
            text(
                """
                SELECT event_type, source_record_namespace
                FROM events
                ORDER BY event_type
                """
            )
        ).all()
        event_count = connection.execute(text("SELECT COUNT(*) FROM events")).scalar_one()
        version_count = connection.execute(
            text("SELECT COUNT(*) FROM event_versions")
        ).scalar_one()
    assert [tuple(event) for event in events] == [
        ("maps_activity", "activity/maps/json"),
        ("search_activity", "activity/search/json"),
    ]
    assert event_count == version_count == 2


def test_google_provider_product_identity_is_stable_across_path_moves(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    export_root = tmp_path / "takeout-20260909T012526Z"
    maps_path = export_root / "first" / "My Activity" / "Maps" / "MyActivity.json"
    youtube_path = (
        export_root / "second" / "My Activity" / "YouTube" / "MyActivity.json"
    )
    maps_path.parent.mkdir(parents=True)
    youtube_path.parent.mkdir(parents=True)
    payload = json.dumps(
        [
            {
                "header": "YouTube",
                "title": "Watched a video",
                "products": ["YouTube"],
            }
        ]
    )
    maps_path.write_text(payload, encoding="utf-8")
    youtube_path.write_text(payload, encoding="utf-8")

    first = import_parsed_exports(database_engine, parse_google_export(maps_path))[0]
    second = import_parsed_exports(database_engine, parse_google_export(youtube_path))[0]

    assert first.inserted_events == 1
    assert second.unchanged_events == 1
    assert second.reused_event_versions == 1
    assert second.conflicted_events == 0
    with database_engine.connect() as connection:
        event = connection.execute(
            text(
                """
                SELECT event_type, source_record_namespace, canonical_revision
                FROM events
                """
            )
        ).one()
        version_count = connection.execute(
            text("SELECT COUNT(*) FROM event_versions")
        ).scalar_one()
    assert tuple(event) == ("youtube_activity", "activity/youtube/json", 1)
    assert version_count == 1


def test_google_later_absence_keeps_prior_activity(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    older_records = [
        {"header": "Search", "title": "First", "time": "2026-09-01T00:00:00Z"},
        {"header": "Maps", "title": "Second", "time": "2026-09-02T00:00:00Z"},
    ]
    newer_records = [older_records[1]]
    first_path = tmp_path / "first" / "MyActivity.json"
    second_path = tmp_path / "second" / "MyActivity.json"
    first_path.parent.mkdir()
    second_path.parent.mkdir()
    first_path.write_text(json.dumps(older_records), encoding="utf-8")
    second_path.write_text(json.dumps(newer_records), encoding="utf-8")

    import_parsed_exports(database_engine, parse_google_export(first_path))
    import_parsed_exports(database_engine, parse_google_export(second_path))

    with database_engine.connect() as connection:
        canonical_count = connection.execute(text("SELECT COUNT(*) FROM events")).scalar_one()
        version_count = connection.execute(
            text("SELECT COUNT(*) FROM event_versions")
        ).scalar_one()
    assert canonical_count == version_count == 2


def test_google_cli_keeps_completed_members_when_a_later_member_is_invalid(
    database_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "takeout-20260901T000000Z-001.zip"
    with ZipFile(source, "w") as archive:
        archive.writestr(
            "Takeout/My Activity/Search/MyActivity.json",
            json.dumps(
                [
                    {
                        "header": "Search",
                        "title": "Committed activity",
                        "time": "2026-09-01T00:00:00Z",
                    }
                ]
            ),
        )
        archive.writestr(
            "Takeout/My Activity/YouTube/MyActivity.json",
            '[{"title":"truncated"}',
        )
    monkeypatch.setattr(cli, "create_database_engine", lambda url: database_engine)
    monkeypatch.setattr(cli, "database_url", lambda override: "ignored")

    with pytest.raises(GoogleExportError, match="ended unexpectedly"):
        cli.main(["import-google", str(source)])

    with database_engine.connect() as connection:
        event_count = connection.execute(text("SELECT COUNT(*) FROM events")).scalar_one()
        run_count = connection.execute(text("SELECT COUNT(*) FROM import_runs")).scalar_one()
    assert event_count == run_count == 1


def test_durable_records_preserve_versions_stale_evidence_and_later_absence(
    database_engine: Engine,
) -> None:
    def exported(sha: str, exported_at: datetime, title: str | None) -> ParsedExport:
        records = (
            ()
            if title is None
            else (
                NormalizedRecord(
                    source="synthetic",
                    record_type="contact",
                    source_record_namespace="contacts",
                    source_record_id="contact-1",
                    title=title,
                ),
            )
        )
        return ParsedExport(
            source="synthetic",
            parser_version="synthetic-record-v1",
            filename=f"{sha[0]}.json",
            original_path=f"synthetic://{sha[0]}.json",
            sha256=sha,
            size_bytes=1,
            exported_at=exported_at,
            snapshot_kind="complete",
            snapshot_scope="account/contacts",
            records=records,
        )

    first = exported("1" * 64, datetime(2025, 1, 1, tzinfo=UTC), "Ada")
    newer = exported("2" * 64, datetime(2026, 1, 1, tzinfo=UTC), "Ada Lovelace")
    stale = exported("3" * 64, datetime(2024, 1, 1, tzinfo=UTC), "A. Lovelace")
    absent = exported("4" * 64, datetime(2027, 1, 1, tzinfo=UTC), None)
    conflict = exported("5" * 64, datetime(2026, 1, 1, tzinfo=UTC), "Ada Byron")

    inserted = import_parsed_exports(database_engine, (first,))[0]
    updated = import_parsed_exports(database_engine, (newer,))[0]
    stale_result = import_parsed_exports(database_engine, (stale,))[0]
    conflict_result = import_parsed_exports(database_engine, (conflict,))[0]
    absent_result = import_parsed_exports(database_engine, (absent,))[0]
    repeated = import_parsed_exports(database_engine, (newer,))[0]

    assert inserted.inserted_records == 1
    assert updated.updated_records == 1
    assert stale_result.stale_records == 1
    assert conflict_result.conflicted_records == 1
    assert absent_result.source_records == absent_result.database_records == 0
    assert repeated.short_circuited
    with database_engine.connect() as connection:
        canonical = connection.execute(
            text("SELECT title, canonical_revision, lifecycle_state FROM records")
        ).one()
        version_count = connection.execute(text("SELECT COUNT(*) FROM record_versions")).scalar_one()
        observations = connection.execute(
            text(
                """
                SELECT ro.outcome, rv.normalized_payload ->> 'title'
                FROM record_observations ro
                JOIN record_versions rv ON rv.id = ro.record_version_id
                ORDER BY ro.import_run_id
                """
            )
        ).all()
    assert tuple(canonical) == ("Ada Lovelace", 2, "active")
    assert int(version_count) == 4
    assert [tuple(row) for row in observations] == [
        ("inserted", "Ada"),
        ("updated", "Ada Lovelace"),
        ("stale", "A. Lovelace"),
        ("conflicted", "Ada Byron"),
    ]


def test_assets_are_external_versioned_references_and_path_moves_are_provenance(
    database_engine: Engine,
) -> None:
    def exported(sha: str, locator: str, exported_at: datetime) -> ParsedExport:
        asset = NormalizedAsset(
            source="google",
            asset_namespace="photos/media",
            source_asset_id="provider-photo-1",
            sha256="f" * 64,
            mime_type="image/jpeg",
            filename="photo.jpg",
            original_path=locator,
            size_bytes=10_000_000_000,
            metadata={"google": {"favorite": True}},
        )
        return ParsedExport(
            source="google",
            parser_version="synthetic-asset-v1",
            filename="photo-sidecar.json",
            original_path=locator + ".json",
            sha256=sha,
            size_bytes=1,
            exported_at=exported_at,
            snapshot_kind="partial",
            snapshot_scope="account/photos",
            assets=(asset,),
        )

    first = exported(
        "a" * 64,
        "C:/takeout-a.zip!/Takeout/Google Photos/photo.jpg",
        datetime(2025, 1, 1, tzinfo=UTC),
    )
    moved = exported(
        "b" * 64,
        "D:/takeout-b.zip!/Takeout/Google Photos/photo.jpg",
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    first_result = import_parsed_exports(database_engine, (first,))[0]
    moved_result = import_parsed_exports(database_engine, (moved,))[0]

    assert first_result.inserted_assets == 1
    assert moved_result.unchanged_assets == 1
    with database_engine.connect() as connection:
        asset = connection.execute(
            text("SELECT original_path, size_bytes, sha256 FROM assets")
        ).one()
        versions = connection.execute(text("SELECT COUNT(*) FROM asset_versions")).scalar_one()
        observations = connection.execute(text("SELECT COUNT(*) FROM asset_observations")).scalar_one()
    assert tuple(asset) == (
        "D:/takeout-b.zip!/Takeout/Google Photos/photo.jpg",
        10_000_000_000,
        "f" * 64,
    )
    assert int(versions) == 1
    assert int(observations) == 2


def test_relationships_are_versioned_provenance_bearing_and_idempotent(
    database_engine: Engine,
) -> None:
    def exported(sha: str, exported_at: datetime, source_order: int) -> ParsedExport:
        relationship = NormalizedRelationship(
            source="google",
            relationship_type="saved_list_contains_item",
            source_relationship_namespace="google_takeout/saved/contains",
            source_relationship_id="list-1:item-1",
            subject_kind="record",
            subject_source="google",
            subject_namespace="google_takeout/saved/saved_list/csv",
            subject_source_id="provider:list-1",
            object_kind="record",
            object_source="google",
            object_namespace="google_takeout/saved/saved_item/csv",
            object_source_id="provider:item-1",
            metadata={"_pdl": {"source_order": source_order}},
        )
        return ParsedExport(
            source="google",
            parser_version="google-geo-v2",
            filename="saved.csv",
            original_path=f"synthetic://{sha[0]}.csv",
            sha256=sha,
            size_bytes=1,
            exported_at=exported_at,
            snapshot_kind="partial",
            snapshot_scope="account/takeout/saved",
            relationships=(relationship,),
        )

    first = exported("6" * 64, datetime(2025, 1, 1, tzinfo=UTC), 1)
    newer = exported("7" * 64, datetime(2026, 1, 1, tzinfo=UTC), 2)

    inserted = import_parsed_exports(database_engine, (first,))[0]
    updated = import_parsed_exports(database_engine, (newer,))[0]
    repeated = import_parsed_exports(database_engine, (newer,))[0]

    assert inserted.inserted_relationships == 1
    assert updated.updated_relationships == 1
    assert repeated.short_circuited
    assert repeated.source_relationships == repeated.database_relationships == 1
    with database_engine.connect() as connection:
        canonical = connection.execute(
            text(
                """
                SELECT relationship_type, metadata -> '_pdl' ->> 'source_order',
                       canonical_revision
                FROM relationships
                """
            )
        ).one()
        versions = connection.execute(
            text("SELECT COUNT(*) FROM relationship_versions")
        ).scalar_one()
        observations = connection.execute(
            text("SELECT COUNT(*) FROM relationship_observations")
        ).scalar_one()
    assert tuple(canonical) == ("saved_list_contains_item", "2", 2)
    assert int(versions) == 2
    assert int(observations) == 2


def _apple_zip(path: Path, entries: list[tuple[str, str | bytes]]) -> Path:
    with ZipFile(path, "w") as archive:
        for name, body in entries:
            archive.writestr(name, body)
    return path


def _apple_counts(engine: Engine) -> dict[str, int]:
    tables = (
        "records",
        "record_versions",
        "events",
        "event_versions",
        "assets",
        "asset_versions",
        "relationships",
        "relationship_versions",
        "import_runs",
    )
    with engine.connect() as connection:
        return {
            table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in tables
        }


def test_apple_identical_notes_and_attachments_persist_all_contexts_and_replay(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    from personal_data_lab.apple_export.orchestrator import import_apple_all

    source = _apple_zip(
        tmp_path / "iCloud Notes.zip",
        [
            ("Notes/A/note.txt", "same body"),
            ("Notes/B/note.txt", "same body"),
            ("Notes/A/image.jpg", b"same image"),
            ("Notes/B/image.jpg", b"same image"),
        ],
    )
    first = import_apple_all(database_engine, str(source))
    assert first.exit_code == 0
    before = _apple_counts(database_engine)
    assert before["records"] == before["record_versions"] == 4
    assert before["assets"] == before["asset_versions"] == 1
    assert before["relationships"] == before["relationship_versions"] == 4
    assert before["import_runs"] == 4
    second = import_apple_all(database_engine, str(source))
    assert second.members_short_circuited == 4
    assert second.exit_code == 0
    assert _apple_counts(database_engine) == before


def test_apple_changed_packaging_and_smaller_exports_do_not_conflict_or_delete(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    from personal_data_lab.apple_export.orchestrator import import_apple_all

    header = "Device Identifier,Event Date,Action\n"
    a, b, c = "d1,2026-01-01,play\n", "d1,2026-01-02,stop\n", "d1,2026-01-03,play\n"
    source = _apple_zip(tmp_path / "Other Data Part 1.zip", [("history.csv", header + a + b)])
    first = import_apple_all(database_engine, str(source))
    assert first.exit_code == 0
    assert first.production_ready
    assert first.coverage["other_apple_data"].status == "losslessly_structured"
    assert first.coverage["other_apple_data"].interpretation_status == "generic_lossless"
    source = _apple_zip(
        tmp_path / "Other Data Part 2.zip",
        [
            ("unrelated/", ""),
            ("history.csv", header + c + b + a),
        ],
    )
    summary = import_apple_all(database_engine, str(source))
    assert summary.members_failed == 0
    assert not any(item.conflicts for item in summary.coverage.values())
    counts = _apple_counts(database_engine)
    assert counts["records"] == counts["record_versions"] == 3
    assert counts["events"] == counts["event_versions"] == 3
    source = _apple_zip(source, [("history.csv", header + b)])
    smaller = import_apple_all(database_engine, str(source))
    assert not any(item.conflicts for item in smaller.coverage.values())
    after = _apple_counts(database_engine)
    assert after["records"] == after["record_versions"] == 3
    assert after["events"] == after["event_versions"] == 3


def test_apple_repackaged_identical_notes_reuse_runs_and_add_source_locations(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    from personal_data_lab.apple_export.orchestrator import import_apple_all

    source = _apple_zip(tmp_path / "iCloud Notes Part 1.zip", [("Notes/A/note.txt", "body")])
    assert import_apple_all(database_engine, str(source)).exit_code == 0
    before = _apple_counts(database_engine)
    source = _apple_zip(
        tmp_path / "iCloud Notes Part 2.zip",
        [
            ("unrelated/", ""),
            ("Notes/A/note.txt", "body"),
        ],
    )
    summary = import_apple_all(database_engine, str(source))
    assert summary.members_short_circuited == 1
    assert _apple_counts(database_engine) == before
    with database_engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM source_file_locations")).scalar_one() == 2
        )


def test_apple_csv_interruption_resumes_committed_chunks_and_replays(
    database_engine: Engine,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from personal_data_lab.apple_export import adapters
    from personal_data_lab.apple_export.orchestrator import import_apple_all

    monkeypatch.setattr(adapters, "CSV_BATCH_ROWS", 2)
    source = _apple_zip(
        tmp_path / "Other Data.zip",
        [
            (
                "history.csv",
                "Device Identifier,Event Date,Action\n"
                + "".join(f"d1,2026-01-0{index},play\n" for index in range(1, 6)),
            )
        ],
    )

    def interrupt(member, parsed, result):
        raise KeyboardInterrupt("synthetic interruption after committed chunk")

    with pytest.raises(KeyboardInterrupt):
        import_apple_all(database_engine, str(source), on_member_imported=interrupt)
    interrupted = _apple_counts(database_engine)
    assert interrupted["records"] == interrupted["record_versions"] == 2
    assert interrupted["import_runs"] == 1
    resumed = import_apple_all(database_engine, str(source))
    assert resumed.members_failed == 0
    assert resumed.members_imported == 1
    complete = _apple_counts(database_engine)
    assert complete["records"] == complete["record_versions"] == 5
    assert complete["events"] == complete["event_versions"] == 5
    assert complete["import_runs"] == 3
    assert import_apple_all(database_engine, str(source)).members_short_circuited == 1
    assert _apple_counts(database_engine) == complete
