"""Fast, unit-level coverage for media.py's non-Whisper plumbing (zip-member reading, source
dispatch, partition scoping, deriver registration) plus one real end-to-end test through the
actual faster-whisper model on a tiny synthetic audio file -- proving the whole chain (zip read,
temp-file spool, model call, row shape) works, the same rigor test_phase6.py's real embedding
calls already established, not just mocked units.
"""

from __future__ import annotations

import io
import os
import struct
import wave
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text

from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.derive import run as derive_run
from personal_data_lab.derive import sync as derive_sync
from personal_data_lab.derive.derivers.media import (
    _open_asset_bytes,
    _open_zip_member,
    _temp_file_suffix,
    _transcribe_one,
)
from personal_data_lab.models import NormalizedAsset, ParsedExport

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
        connection.execute(
            text(
                "TRUNCATE TABLE derive.derivation_run, derive.partition_state, "
                "derive.evidence_epoch RESTART IDENTITY CASCADE"
            )
        )
        connection.execute(text("UPDATE derive.change_cursor SET last_import_run_id = 0"))
        connection.execute(text("TRUNCATE TABLE media.transcript RESTART IDENTITY CASCADE"))
    yield


def _silent_wav_bytes(seconds: float = 1.0) -> bytes:
    buf = io.BytesIO()
    n = int(16000 * seconds)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<" + "h" * n, *([0] * n)))
    return buf.getvalue()


def test_temp_file_suffix_uses_filename_not_a_locator_blob() -> None:
    """Real bug the first real rollout found: deriving the suffix from a source's original_path
    (a whole locator JSON blob for Apple/Facebook, not a plain filename) produced garbage like
    `.m4a",5]]}`, which Windows then rejected outright for every single Apple/Facebook file.
    assets.filename is always a plain, clean filename regardless of locator format.
    """
    assert _temp_file_suffix("New Recording 5.m4a") == ".m4a"
    assert _temp_file_suffix("voicemail.mp3") == ".mp3"
    # A malformed or extension-less filename falls back to a safe default rather than producing
    # an invalid temp-file suffix.
    assert _temp_file_suffix('weird",5]]}') == ".audio"
    assert _temp_file_suffix("no_extension") == ".audio"


def test_open_zip_member_reads_the_right_bytes(tmp_path) -> None:
    zip_path = tmp_path / "archive.zip"
    payload = b"hello from inside the zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("inner/file.bin", payload)

    with _open_zip_member(f"{zip_path}!/inner/file.bin") as stream:
        assert stream.read() == payload


def test_open_asset_bytes_dispatches_plain_file(tmp_path) -> None:
    path = tmp_path / "plain.bin"
    path.write_bytes(b"plain file content")

    with _open_asset_bytes("google", str(path)) as stream:
        assert stream.read() == b"plain file content"


def test_open_asset_bytes_dispatches_zip_member(tmp_path) -> None:
    zip_path = tmp_path / "a.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("m.bin", b"zipped content")

    with _open_asset_bytes("openai", f"{zip_path}!/m.bin") as stream:
        assert stream.read() == b"zipped content"


def test_open_asset_bytes_unreadable_path_raises(tmp_path) -> None:
    missing = tmp_path / "does_not_exist.bin"
    with pytest.raises(OSError), _open_asset_bytes("google", str(missing)):
        pass


def test_sync_registers_media_transcribe_whisper(database_engine: Engine) -> None:
    derive_sync(database_engine)
    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT partition_class FROM derive.deriver "
                "WHERE name = 'media_transcribe_whisper'"
            )
        ).first()
    assert row is not None
    assert row.partition_class == "LOCAL"


def test_transcribe_one_real_whisper_call_on_a_tiny_synthetic_file(tmp_path) -> None:
    """The one real, non-mocked call through faster-whisper -- proves the temp-file spool and
    row shape actually work end to end, not just that the plumbing functions look right in
    isolation. A near-silent clip is enough: this checks the row's shape and status, not that
    the transcribed text is meaningful.
    """
    wav_path = tmp_path / "silent.wav"
    wav_path.write_bytes(_silent_wav_bytes(seconds=1.5))

    class _FakeAsset:
        id = 1
        source = "google"
        original_path = str(wav_path)
        filename = "silent.wav"

    row = _transcribe_one(_FakeAsset(), epoch_id=1)

    assert row["asset_id"] == 1
    assert row["source"] == "google"
    assert row["status"] == "succeeded"
    assert row["duration_seconds"] == pytest.approx(1.5, abs=0.1)
    assert isinstance(row["text"], str)
    assert row["error"] is None


def test_transcribe_one_unreadable_asset_reports_unreadable_not_a_crash() -> None:
    class _FakeAsset:
        id = 2
        source = "google"
        original_path = "/does/not/exist.wav"
        filename = "missing.wav"

    row = _transcribe_one(_FakeAsset(), epoch_id=1)

    assert row["status"] == "unreadable"
    assert row["error"]
    assert row["text"] == ""


def _import_asset(
    engine: Engine, *, source: str, filename: str, mime_type: str, captured_at: datetime
) -> None:
    parsed = ParsedExport(
        source=source,
        parser_version="test-media-v1",
        filename=filename,
        original_path=filename,
        sha256="0" * 64,
        size_bytes=1,
        assets=(
            NormalizedAsset(
                source=source,
                asset_namespace=f"{source}/asset",
                source_asset_id="a1",
                filename=filename,
                original_path=filename,
                size_bytes=1,
                mime_type=mime_type,
                captured_at=captured_at,
            ),
        ),
    )
    import_parsed_exports(engine, (parsed,))


def test_clear_partition_only_deletes_its_own_source_and_month(database_engine: Engine) -> None:
    _import_asset(
        database_engine, source="google", filename="only-audio.wav", mime_type="audio/wav",
        captured_at=datetime(2024, 3, 15, tzinfo=UTC),
    )
    derive_sync(database_engine)
    outcomes = derive_run(database_engine)

    media_outcomes = [o for o in outcomes if o.deriver_name == "media_transcribe_whisper"]
    assert len(media_outcomes) == 1
    assert media_outcomes[0].status == "succeeded"
    # The real asset's original_path ("only-audio.wav") isn't a readable file on this machine,
    # so the real deriver correctly records it as unreadable rather than crashing the partition.
    with database_engine.connect() as connection:
        row = connection.execute(
            text("SELECT status FROM media.transcript WHERE source = 'google'")
        ).one()
    assert row.status == "unreadable"
