"""Real, non-mocked coverage for backup.py: a genuine pg_dump -> encrypt -> decrypt -> pg_restore
round trip through the actual Docker-run Postgres container and the actual `cryptography`
Fernet/PBKDF2 implementation, not a mocked subprocess -- the same rigor test_media.py's real
Whisper call and test_annotation.py's real llama.cpp call already established for their own
features.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from personal_data_lab.backup import BackupError, create_backup, restore_backup
from personal_data_lab.config import DatabaseTargetGuardError
from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.models import NormalizedEvent, ParsedExport

pytestmark = pytest.mark.integration

_PASSPHRASE = "correct horse battery staple test passphrase"


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


@pytest.fixture
def scratch_database_engine() -> Iterator[Engine]:
    admin_url = os.getenv("TEST_DATABASE_URL")
    if not admin_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    db_name = "pdl_backup_restore_test"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text(f"DROP DATABASE IF EXISTS {db_name}"))
        connection.execute(text(f"CREATE DATABASE {db_name}"))
    scratch_url = make_url(admin_url).set(database=db_name)
    engine = create_engine(scratch_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    try:
        yield engine
    finally:
        engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {db_name}"))
        admin_engine.dispose()


def _import_event(engine: Engine, source_record_id: str, text_body: str) -> None:
    parsed = ParsedExport(
        source="google", parser_version="test-backup-v1", filename="a.json",
        original_path="a.json", sha256="b" * 64, size_bytes=1,
        events=(
            NormalizedEvent(
                occurred_at=datetime(2024, 6, 1, tzinfo=UTC), source="google",
                event_type="mail_message", source_record_namespace="google/mail_message",
                source_record_id=source_record_id, text=text_body,
            ),
        ),
    )
    import_parsed_exports(engine, (parsed,))


def test_create_backup_requires_a_passphrase(database_engine: Engine, tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PDL_BACKUP_PASSPHRASE", raising=False)
    with pytest.raises(BackupError, match="PDL_BACKUP_PASSPHRASE"):
        create_backup(str(database_engine.url), output_dir=tmp_path)


def test_restore_refuses_the_canonical_database_name_without_force(tmp_path) -> None:
    fake_file = tmp_path / "fake.dump.enc"
    fake_file.write_bytes(b"not a real backup, guard should fire before reading it meaningfully")
    with pytest.raises(DatabaseTargetGuardError, match="canonical"):
        restore_backup(
            fake_file,
            "postgresql+psycopg://user:pass@127.0.0.1:5432/personaldata",
            passphrase=_PASSPHRASE,
        )


def test_backup_then_restore_round_trips_real_data(
    database_engine: Engine, scratch_database_engine: Engine, tmp_path
) -> None:
    _import_event(database_engine, "e1", "the real event text this test checks for")

    with database_engine.connect() as connection:
        before = connection.execute(
            text("SELECT text FROM events WHERE source_record_id = 'e1'")
        ).scalar_one()
    assert before == "the real event text this test checks for"

    result = create_backup(
        str(database_engine.url), output_dir=tmp_path, passphrase=_PASSPHRASE
    )
    assert result.path.exists()
    assert result.size_bytes > 0
    assert result.database_name == database_engine.url.database

    restore_backup(
        result.path, str(scratch_database_engine.url), passphrase=_PASSPHRASE,
    )

    with scratch_database_engine.connect() as connection:
        after = connection.execute(
            text("SELECT text FROM events WHERE source_record_id = 'e1'")
        ).scalar_one()
    assert after == "the real event text this test checks for"


def test_restore_with_the_wrong_passphrase_fails_to_decrypt(
    database_engine: Engine, scratch_database_engine: Engine, tmp_path
) -> None:
    _import_event(database_engine, "e2", "irrelevant content")
    result = create_backup(
        str(database_engine.url), output_dir=tmp_path, passphrase=_PASSPHRASE
    )

    with pytest.raises(BackupError, match="decrypt"):
        restore_backup(
            result.path, str(scratch_database_engine.url), passphrase="the wrong passphrase",
        )
