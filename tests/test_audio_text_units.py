"""Audio in the semantic layer (migration 029): the device-backup source's recordings become
message-layer text units (chunked, with record/asset provenance, superseded copies left out), and
every other source's Whisper transcripts become chunked document units."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text

from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.derive import sync as derive_sync
from personal_data_lab.derive.derivers.semantic import chunk_text, semantic_text_unit
from personal_data_lab.derive.derivers.semantic_documents import semantic_text_unit_document
from personal_data_lab.models import NormalizedAsset, NormalizedRecord, ParsedExport

pytestmark = pytest.mark.integration

MAY = datetime(2024, 5, 3, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_database_engine(url)
    with engine.connect() as connection:
        name = str(connection.execute(text("SELECT current_database()")).scalar_one())
    if not name.endswith("_test"):
        engine.dispose()
        pytest.fail("integration tests require a database name ending in '_test'")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean(database_engine: Engine) -> Iterator[None]:
    with database_engine.begin() as c:
        c.execute(text(
            "TRUNCATE TABLE media.audio_supersession, media.audio_fingerprint, media.transcript, "
            "semantic.text_unit, semantic.text_unit_document, relationships, assets, records, events, "
            "conversations, source_files, import_runs RESTART IDENTITY CASCADE"
        ))
        c.execute(text("TRUNCATE TABLE derive.derivation_run, derive.partition_state, "
                       "derive.evidence_epoch RESTART IDENTITY CASCADE"))
        c.execute(text("UPDATE derive.change_cursor SET last_import_run_id = 0"))
    yield


def _import(engine: Engine, source: str, *, records=(), assets=(), sha: str = "d") -> None:
    import_parsed_exports(engine, (ParsedExport(
        source=source, parser_version="test-audio-v1", filename="f", original_path="f",
        sha256=sha * 64, size_bytes=1, records=tuple(records), assets=tuple(assets),
    ),))


def _asset(source: str, asset_id: str, captured_at) -> NormalizedAsset:
    return NormalizedAsset(source=source, asset_namespace=f"{source}/audio", source_asset_id=asset_id,
                           mime_type="audio/mp4", filename=f"{asset_id}.m4a", original_path=f"{asset_id}.m4a",
                           size_bytes=1, captured_at=captured_at)


def _transcribe(engine: Engine, epoch: int, source_asset_id: str, body: str) -> int:
    with engine.begin() as c:
        asset_id = c.execute(text("SELECT id FROM assets WHERE source_asset_id = :s"),
                             {"s": source_asset_id}).scalar_one()
        c.execute(text(
            "INSERT INTO media.transcript (asset_id, source, model, text, status, computed_epoch_id) "
            "SELECT id, source, 'test', :t, 'succeeded', :e FROM assets WHERE id = :a"
        ), {"t": body, "e": epoch, "a": asset_id})
    return asset_id


def _epoch(engine: Engine) -> int:
    derive_sync(engine)
    with engine.connect() as c:
        return c.execute(text("SELECT max(id) FROM derive.evidence_epoch")).scalar_one()


def test_chunker_keeps_every_character_and_breaks_on_sentences() -> None:
    body = "One sentence here. " * 700
    chunks = chunk_text(body, 6_000)
    assert len(chunks) == 3 and all(len(c) <= 6_000 for c in chunks)
    assert all(c.endswith(".") for c in chunks)
    assert "".join(c.replace(" ", "") for c in chunks) == body.replace(" ", "")


def test_device_recordings_become_message_units_with_provenance(database_engine: Engine) -> None:
    _import(database_engine, "ios_backup", records=[NormalizedRecord(
        source="ios_backup", record_type="voice_memo", source_record_namespace="ios_backup/voice_memos/recording",
        source_record_id="memo-1", text="We talked about the lease. " * 400, occurred_at=MAY,
        metadata={"duration_seconds": 600, "transcript_source": "apple_on_device"},
    )], assets=[_asset("ios_backup", "kept", MAY), _asset("ios_backup", "copy", MAY)])
    epoch = _epoch(database_engine)
    kept = _transcribe(database_engine, epoch, "kept", "A Whisper transcript of a MOTIV take.")
    copy = _transcribe(database_engine, epoch, "copy", "The same take, trimmed.")
    with database_engine.begin() as c:
        c.execute(text("INSERT INTO media.audio_supersession (asset_id, kept_asset_id, relation, detector_version) "
                       "VALUES (:a, :k, 'contained_in', 'test')"), {"a": copy, "k": kept})

    with database_engine.connect() as c:
        result = semantic_text_unit(c, "ios_backup:2024-05", epoch)
    rows = result.rows
    memo = [r for r in rows if r["record_id"] is not None]
    audio = [r for r in rows if r["asset_id"] is not None]
    assert [r["chunk_index"] for r in memo] == [0, 1]
    assert all(r["event_ids"] == [] and r["conversation_id"] is None for r in rows)
    assert memo[0]["ended_at"] > memo[0]["started_at"]
    assert [r["asset_id"] for r in audio] == [kept]  # the superseded copy never becomes a unit


def test_other_sources_transcripts_become_undated_document_units(database_engine: Engine) -> None:
    _import(database_engine, "google", assets=[_asset("google", "recorder-1", None)])
    _import(database_engine, "ios_backup", assets=[_asset("ios_backup", "phone-1", None)], sha="e")
    epoch = _epoch(database_engine)
    google = _transcribe(database_engine, epoch, "recorder-1", "Speaker talking. " * 700)
    _transcribe(database_engine, epoch, "phone-1", "Device audio goes to the message layer instead.")

    with database_engine.connect() as c:
        google_rows = semantic_text_unit_document(c, "google:undated", epoch).rows
        phone_rows = semantic_text_unit_document(c, "ios_backup:undated", epoch).rows
    assert {r["asset_id"] for r in google_rows} == {google}
    assert [r["chunk_index"] for r in google_rows] == [0, 1]
    assert all(r["record_id"] is None and r["started_at"] is None for r in google_rows)
    assert phone_rows == []
