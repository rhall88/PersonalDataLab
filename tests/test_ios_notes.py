"""Apple Notes from the phone: body decoding (gzip and zlib protobuf, checklists), the import of
notes, call-recording transcripts and media files, and the notes' reach into text units."""

from __future__ import annotations

import gzip
import os
import zlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from test_ios_backup import Backup

from personal_data_lab.database import create_database_engine
from personal_data_lab.derive import sync as derive_sync
from personal_data_lab.derive.derivers.semantic import semantic_text_unit
from personal_data_lab.ios_backup.notes import decode_note_body
from personal_data_lab.ios_backup.orchestrator import import_ios_notes

pytestmark = pytest.mark.integration
NOTES = "AppDomainGroup-group.com.apple.notes"
CREATED = 770_000_000.0  # Core Data seconds: 2025-05-27


def _v(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _ld(number: int, payload: bytes) -> bytes:
    return _v(number << 3 | 2) + _v(len(payload)) + payload


def _vi(number: int, value: int) -> bytes:
    return _v(number << 3) + _v(value)


def _run(length: int, style: int | None = None, done: bool = False) -> bytes:
    body = _vi(1, length)
    if style is not None:
        paragraph = _vi(1, style) + (_ld(5, _ld(1, b"u") + _vi(2, int(done))) if style == 103 else b"")
        body += _ld(2, paragraph)
    return _ld(5, body)


def _blob(body: str, runs: bytes, *, zlibbed: bool = False) -> bytes:
    note = _ld(2, body.encode()) + runs
    root = _vi(1, 0) + _ld(2, _vi(1, 0) + _ld(3, note))
    return zlib.compress(root) if zlibbed else gzip.compress(root)


def _units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def test_body_decoding_finds_checklist_items_by_paragraph() -> None:
    body = "Groceries\nMilk\nEggs \U0001f95a\nplain"
    runs = (_run(_units("Groceries\n")) + _run(_units("Milk\n"), 103, True)
            + _run(_units("Eggs \U0001f95a"), 103) + _run(1) + _run(_units("plain")))
    decoded = decode_note_body(_blob(body, runs))
    assert decoded is not None and decoded.text == body
    assert decoded.checklist == [{"text": "Milk", "done": True}, {"text": "Eggs \U0001f95a", "done": False}]
    framed = decode_note_body(_blob("zlib framed", _run(11), zlibbed=True))
    assert framed is not None and framed.text == "zlib framed"
    assert decode_note_body(b"garbage") is None


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
        c.execute(text("TRUNCATE TABLE media.transcript, semantic.text_unit, relationships, assets, records, "
                       "events, conversations, source_files, import_runs RESTART IDENTITY CASCADE"))
        c.execute(text("TRUNCATE TABLE derive.derivation_run, derive.partition_state, "
                       "derive.evidence_epoch RESTART IDENTITY CASCADE"))
        c.execute(text("UPDATE derive.change_cursor SET last_import_run_id = 0"))
    yield


def _note_store(backup: Backup) -> None:
    columns = ("Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZIDENTIFIER TEXT, ZTITLE1 TEXT, ZTITLE2 TEXT, "
               "ZTITLE TEXT, ZSNIPPET TEXT, ZFOLDER INTEGER, ZACCOUNT7 INTEGER, ZNAME TEXT, ZFOLDERTYPE INTEGER, "
               "ZCREATIONDATE3 REAL, ZMODIFICATIONDATE1 REAL, ZISPASSWORDPROTECTED INTEGER, "
               "ZMARKEDFORDELETION INTEGER, ZNOTE INTEGER, ZMEDIA INTEGER, ZTYPEUTI TEXT, ZSUMMARY TEXT, "
               "ZADDITIONALINDEXABLETEXT TEXT, ZFILENAME TEXT, ZCREATIONDATE REAL")
    names = [c.split()[0] for c in columns.split(", ")]

    def row(**values: object) -> tuple[str, tuple]:
        marks = ", ".join("?" for _ in names)
        return f"INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES ({marks})", tuple(values.get(n) for n in names)

    def note(pk: int, ident: str, title: str, folder: int, locked: int = 0) -> tuple[str, tuple]:
        return row(Z_PK=pk, Z_ENT=12, ZIDENTIFIER=ident, ZTITLE1=title, ZSNIPPET="snip", ZFOLDER=folder,
                   ZACCOUNT7=1, ZCREATIONDATE3=CREATED, ZMODIFICATIONDATE1=CREATED + 60,
                   ZISPASSWORDPROTECTED=locked, ZMARKEDFORDELETION=0)

    statements: list[tuple[str, tuple]] = [
        (f"CREATE TABLE ZICCLOUDSYNCINGOBJECT ({columns})", ()),
        ("CREATE TABLE ZICNOTEDATA (Z_PK INTEGER PRIMARY KEY, ZNOTE INTEGER, ZDATA BLOB)", ()),
        row(Z_PK=1, Z_ENT=14, ZNAME="iCloud"),
        row(Z_PK=2, Z_ENT=15, ZTITLE2="Notes", ZFOLDERTYPE=0),
        row(Z_PK=3, Z_ENT=15, ZTITLE2="Recently Deleted", ZFOLDERTYPE=1),
        note(10, "N-LIST", "Groceries", 2),
        note(11, "N-ZLIB", "Plain", 2),
        note(12, "N-LOCK", "Private", 2, locked=1),
        note(13, "N-GONE", "Trashed", 3),
        note(14, "N-CALL", "Call with Sarah", 2),
        row(Z_PK=20, Z_ENT=11, ZIDENTIFIER="MEDIA-1", ZFILENAME="call.m4a"),
        row(Z_PK=21, Z_ENT=5, ZIDENTIFIER="ATT-1", ZTITLE="Call with Sarah", ZNOTE=14, ZMEDIA=20,
            ZTYPEUTI="com.apple.m4a-audio", ZADDITIONALINDEXABLETEXT="Sarah\nWhat is going on?",
            ZCREATIONDATE=CREATED),
        ("INSERT INTO ZICNOTEDATA VALUES (1, 10, ?)",
         (_blob("Groceries\nMilk", _run(_units("Groceries\n")) + _run(4, 103, True)),)),
        ("INSERT INTO ZICNOTEDATA VALUES (2, 11, ?)",
         (_blob("A plain note about the lease.", _run(29), zlibbed=True),)),
        ("INSERT INTO ZICNOTEDATA VALUES (3, 12, ?)", (b"encrypted",)),
        ("INSERT INTO ZICNOTEDATA VALUES (4, 13, ?)", (_blob("Thrown away", _run(11)),)),
        ("INSERT INTO ZICNOTEDATA VALUES (5, 14, ?)", (_blob("￼", _run(1)),)),
    ]
    backup.add_db(NOTES, "NoteStore.sqlite", statements)
    backup.add(NOTES, "Accounts/ACCT/Media/MEDIA-1/1_X/call.m4a", b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 16)


def test_notes_import_records_transcripts_media_and_links(tmp_path: Path, database_engine: Engine) -> None:
    backup = Backup(tmp_path / "backup")
    _note_store(backup)
    summary = import_ios_notes(database_engine, backup.root)
    assert (summary.notes, summary.locked, summary.deleted, summary.call_recordings) == (5, 1, 1, 1)
    assert summary.media_files == 1 and summary.media_assets_imported == 1

    with database_engine.connect() as c:
        notes = {r.source_record_id: r for r in c.execute(text(
            "SELECT source_record_id, title, text, lifecycle_state, metadata FROM records "
            "WHERE record_type = 'note'")).all()}
        assert notes["N-LIST"].metadata["checklist"] == [{"text": "Milk", "done": True}]
        assert notes["N-LIST"].metadata["folder"] == "Notes" and notes["N-LIST"].metadata["account"] == "iCloud"
        assert notes["N-ZLIB"].text == "A plain note about the lease."
        assert notes["N-LOCK"].text is None and notes["N-LOCK"].metadata["locked"] is True
        assert notes["N-GONE"].lifecycle_state == "provider_deleted"
        call = c.execute(text(
            "SELECT title, text, metadata FROM records WHERE record_type = 'call_recording'")).one()
        assert call.text.startswith("Sarah") and call.metadata["transcript_source"] == "apple_notes"
        links = dict(c.execute(text(
            "SELECT relationship_type, count(*) FROM relationships WHERE source = 'ios_backup' GROUP BY 1")).all())
        assert links == {"note_has_attachment": 1, "note_attachment_has_file": 1}

    assert import_ios_notes(database_engine, backup.root).media_already_assets == 1


def test_notes_become_text_units_and_the_call_is_not_counted_twice(tmp_path: Path, database_engine: Engine) -> None:
    backup = Backup(tmp_path / "backup")
    _note_store(backup)
    import_ios_notes(database_engine, backup.root)
    derive_sync(database_engine)
    with database_engine.connect() as c:
        epoch = c.execute(text("SELECT max(id) FROM derive.evidence_epoch")).scalar_one()
        asset = c.execute(text("SELECT id FROM assets WHERE source = 'ios_backup'")).scalar_one()
    with database_engine.begin() as c:
        # An earlier Whisper transcript of the same recording must not become a second unit.
        c.execute(text("INSERT INTO media.transcript (asset_id, source, model, text, status, computed_epoch_id) "
                       "VALUES (:a, 'ios_backup', 'test', 'whisper copy of the call', 'succeeded', :e)"),
                  {"a": asset, "e": epoch})
    with database_engine.connect() as c:
        rows = semantic_text_unit(c, "ios_backup:2025-05", epoch).rows
    bodies = sorted(r["text"] for r in rows)
    assert any("lease" in b for b in bodies) and any("What is going on?" in b for b in bodies)
    assert not any("whisper copy" in b for b in bodies)
    assert not any("Thrown away" in b for b in bodies)  # deleted notes are not active
