from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from zipfile import ZipFile

import pytest
from sqlalchemy import Engine, text

from personal_data_lab.anthropic_export.orchestrator import import_anthropic_all
from personal_data_lab.database import create_database_engine

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
            )
        )
    yield


def _write_export(directory: Path) -> Path:
    directory.mkdir()
    conversations = []
    for index in range(30):
        message = {
            "uuid": f"message-{index:02d}",
            "sender": "human",
            "content": [{"type": "text", "text": f"message body {index:02d}"}],
        }
        if index == 0:
            message["files"] = [{"uuid": "missing-1", "file_name": "missing.txt"}]
        conversations.append(
            {
                "uuid": f"conversation-{index:02d}",
                "name": f"conversation {index:02d}",
                "updated_at": "2026-09-09T02:00:00Z",
                "chat_messages": [message],
            }
        )
    archive_path = directory / "conversations-000.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("conversations.json", json.dumps(conversations))
    manifest = {
        "uuid": "export-1",
        "created_at": "2026-09-09T02:08:46Z",
        "data_files": [{"category": "conversations", "filename": archive_path.name}],
    }
    manifest_path = directory / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _counts(engine: Engine) -> dict[str, int]:
    names = (
        "source_files",
        "import_runs",
        "conversations",
        "conversation_versions",
        "events",
        "event_versions",
        "assets",
        "asset_versions",
        "relationships",
        "relationship_versions",
    )
    with engine.connect() as connection:
        return {
            name: int(connection.execute(text(f"SELECT count(*) FROM {name}")).scalar_one())
            for name in names
        }


def test_anthropic_interruption_resumes_and_exact_replay_short_circuits(
    database_engine: Engine,
    tmp_path: Path,
) -> None:
    manifest = _write_export(tmp_path / "anthropic-export")
    interrupted = False

    def interrupt_after_conversation_chunk(source_id, parsed, result):
        nonlocal interrupted
        if not interrupted and "/chunk/" in str(parsed.category):
            interrupted = True
            raise KeyboardInterrupt("synthetic interruption after committed Anthropic chunk")

    with pytest.raises(KeyboardInterrupt):
        import_anthropic_all(
            database_engine,
            str(manifest),
            on_member_imported=interrupt_after_conversation_chunk,
        )
    partial = _counts(database_engine)
    assert partial["conversations"] == partial["conversation_versions"] == 25
    assert partial["events"] == partial["event_versions"] == 25

    resumed = import_anthropic_all(database_engine, str(manifest))
    assert resumed.exit_code == 0
    assert resumed.production_ready
    assert resumed.conversations == 30
    assert resumed.events == 30
    assert resumed.assets == 1
    assert resumed.relationships == 1
    complete = _counts(database_engine)
    assert complete["source_files"] == 2
    assert complete["import_runs"] == 3
    assert complete["conversations"] == complete["conversation_versions"] == 30
    assert complete["events"] == complete["event_versions"] == 30
    assert complete["assets"] == complete["asset_versions"] == 1
    assert complete["relationships"] == complete["relationship_versions"] == 1

    replay = import_anthropic_all(database_engine, str(manifest))
    assert replay.exit_code == 0
    assert replay.files_short_circuited == 2
    assert _counts(database_engine) == complete
