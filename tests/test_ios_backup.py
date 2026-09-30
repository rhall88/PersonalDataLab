"""iOS device backup source: inventory, Voice Memo / voicemail transcripts, asset provenance."""

from __future__ import annotations

import hashlib
import json
import plistlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from personal_data_lab.database import ImportResult
from personal_data_lab.ios_backup import orchestrator
from personal_data_lab.ios_backup.adapters import (
    CORE_DATA_EPOCH,
    audio_export,
    voice_memo_transcript,
    voice_memos_export,
    voicemail_export,
)
from personal_data_lab.ios_backup.inventory import IosBackupError, inspect_ios_backup

VM_DOMAIN = "AppDomainGroup-group.com.apple.VoiceMemos.shared"


def _atom(kind: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def _m4a(transcript_runs: list | None) -> bytes:
    udta = _atom(b"tsrp", json.dumps({"attributedString": {"runs": transcript_runs}}).encode()) if transcript_runs else b""
    return _atom(b"ftyp", b"M4A \x00\x00\x00\x00") + _atom(b"moov", _atom(b"udta", udta)) + _atom(b"mdat", b"\x00" * 32)


def _keyed_archive(root: dict, extra: list) -> bytes:
    objects = ["$null", root, *extra]
    return plistlib.dumps({"$version": 100000, "$archiver": "NSKeyedArchiver", "$top": {"root": plistlib.UID(1)},
                           "$objects": objects}, fmt=plistlib.FMT_BINARY)


class Backup:
    def __init__(self, root: Path, encrypted: bool = False) -> None:
        self.root = root
        root.mkdir()
        with (root / "Manifest.plist").open("wb") as stream:
            plistlib.dump({"IsEncrypted": encrypted, "Date": datetime(2026, 9, 30, tzinfo=UTC),
                           "Lockdown": {"DeviceName": "Test", "ProductType": "iPhone14,2",
                                        "ProductVersion": "26.6.1", "UniqueDeviceID": "UDID"}}, stream)
        self.manifest = sqlite3.connect(root / "Manifest.db")
        self.manifest.execute("CREATE TABLE Files (fileID TEXT PRIMARY KEY, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB)")

    def add(self, domain: str, relative_path: str, data: bytes) -> Path:
        file_id = hashlib.sha1(f"{domain}-{relative_path}".encode()).hexdigest()
        path = self.root / file_id[:2] / file_id
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        self.manifest.execute("INSERT INTO Files VALUES (?, ?, ?, 1, NULL)", (file_id, domain, relative_path))
        self.manifest.commit()
        return path

    def add_db(self, domain: str, relative_path: str, statements: list[tuple[str, tuple]]) -> None:
        scratch = self.root.parent / f"{hashlib.sha1(relative_path.encode()).hexdigest()}.sqlite"
        db = sqlite3.connect(scratch)
        for sql, params in statements:
            db.execute(sql, params)
        db.commit()
        db.close()
        self.add(domain, relative_path, scratch.read_bytes())


@pytest.fixture
def backup(tmp_path: Path) -> Backup:
    b = Backup(tmp_path / "backup")
    memo_date = datetime(2026, 5, 1, 12, 0, tzinfo=UTC).timestamp() - CORE_DATA_EPOCH
    b.add(VM_DOMAIN, "Recordings/20260501 120000.m4a", _m4a(["Hello", 0, " there.", 1]))
    b.add(VM_DOMAIN, "Recordings/20260502 090000.m4a", _m4a(None))
    b.add_db(VM_DOMAIN, "Recordings/CloudRecordings.db", [
        (("CREATE TABLE ZCLOUDRECORDING (ZUNIQUEID TEXT, ZPATH TEXT, ZDATE REAL, ZDURATION REAL, ZLOCALDURATION REAL, "
          "ZENCRYPTEDTITLE TEXT, ZCUSTOMLABEL TEXT, ZFLAGS INTEGER)"), ()),
        ("INSERT INTO ZCLOUDRECORDING VALUES (?, ?, ?, 0, 42.5, ?, NULL, 0)", ("A", "20260501 120000.m4a", memo_date, "Kitchen talk")),
        ("INSERT INTO ZCLOUDRECORDING VALUES (?, ?, ?, 0, 10, NULL, 'label', 0)", ("B", "20260502 090000.m4a", memo_date + 3600)),
        ("INSERT INTO ZCLOUDRECORDING VALUES (?, ?, ?, 0, 5, 'iCloud only', NULL, 0)", ("C", "20260503 090000.m4a", memo_date + 7200)),
    ])
    b.add("HomeDomain", "Library/Voicemail/7.amr", b"#!AMR\n" + b"\x00" * 20)
    b.add("HomeDomain", "Library/Voicemail/A-ACCOUNT/7.amr", b"#!AMR\n" + b"\x00" * 20)
    b.add("HomeDomain", "Library/Voicemail/7.transcript", _keyed_archive(
        {"transcriptionString": plistlib.UID(2), "confidence": plistlib.UID(3)}, ["Call me back about the lease.", 0.81]))
    b.add_db("HomeDomain", "Library/Voicemail/voicemail.db", [
        (("CREATE TABLE voicemail (ROWID INTEGER PRIMARY KEY, date INTEGER, sender TEXT, callback_num TEXT, receiver TEXT, "
          "duration INTEGER, trashed_date INTEGER, flags INTEGER, uuid TEXT)"), ()),
        ("INSERT INTO voicemail VALUES (7, 1790000000, '+13175550101', NULL, '+13175550199', 12, 0, 0, 'VM-UUID')", ()),
    ])
    same = b"RIFF" + b"\x01" * 40
    b.add("AppDomain-com.shure.plus.motiv", "Documents/take.wav", same)
    b.add("AppDomainGroup-group.com.apple.FileProvider.LocalStorage", "File Provider Storage/take.wav", same)
    b.add("TonesDomain", "Ringtones/ring.m4a", b"x")
    return b


def test_inventory_classifies_audio_by_app_and_skips_ringtones(backup: Backup) -> None:
    inventory = inspect_ios_backup(backup.root)
    counts = {product: n for product, (n, _) in inventory.product_counts().items()}
    assert counts == {"files_app": 1, "motiv": 1, "voice_memos": 2, "voicemail": 2}
    assert inventory.voice_memos_db is not None and inventory.voicemail_db is not None
    assert inventory.ios_version == "26.6.1"


def test_encrypted_backup_is_refused(tmp_path: Path) -> None:
    Backup(tmp_path / "enc", encrypted=True)
    with pytest.raises(IosBackupError, match="encrypted"):
        inspect_ios_backup(tmp_path / "enc")


def test_voice_memo_transcript_comes_from_the_tsrp_atom(tmp_path: Path) -> None:
    (tmp_path / "a.m4a").write_bytes(_m4a(["One", 0, " two.", 1]))
    (tmp_path / "b.m4a").write_bytes(_m4a(None))
    assert voice_memo_transcript(str(tmp_path / "a.m4a")) == "One two."
    assert voice_memo_transcript(str(tmp_path / "b.m4a")) is None


def test_voice_memos_records_carry_title_date_transcript_and_audio_link(backup: Backup) -> None:
    parsed = voice_memos_export(inspect_ios_backup(backup.root))
    by_id = {r.source_record_id: r for r in parsed.records}
    assert by_id["A"].title == "Kitchen talk" and by_id["A"].text == "Hello there."
    assert by_id["A"].occurred_at == datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    assert by_id["A"].metadata["transcript_source"] == "apple_on_device"
    assert by_id["B"].text is None and by_id["B"].title == "label"
    assert by_id["C"].metadata["audio_in_backup"] is False
    assert {r.subject_source_id for r in parsed.relationships} == {"A", "B"}
    assert any("iCloud-only" in w for w in parsed.parse_warnings)


def test_voicemail_record_has_caller_transcript_and_prefers_top_level_audio(backup: Backup) -> None:
    parsed = voicemail_export(inspect_ios_backup(backup.root))
    (record,) = parsed.records
    assert record.text == "Call me back about the lease."
    assert record.actor_identity == "+13175550101"
    assert record.metadata["transcript_confidence"] == pytest.approx(0.81)
    (link,) = parsed.relationships
    assert link.object_source_id == "HomeDomain/Library/Voicemail/7.amr"


def test_duplicate_audio_across_apps_shares_one_content_hash(backup: Backup) -> None:
    inventory = inspect_ios_backup(backup.root)
    wavs = [audio_export(item, inventory) for item in inventory.audio if item.extension == ".wav"]
    assert len(wavs) == 2
    assert wavs[0].assets[0].sha256 == wavs[1].assets[0].sha256
    assert wavs[0].assets[0].source_asset_id != wavs[1].assets[0].source_asset_id


def test_orchestrator_imports_assets_before_databases(backup: Backup, monkeypatch) -> None:
    order: list[str] = []

    def imported(engine, batch):
        (parsed,) = batch
        order.append(parsed.category)
        return (ImportResult(source_file_id=1, source_conversations=0, database_conversations=0, source_events=0,
                             database_events=0, source_records=parsed.record_count, database_records=parsed.record_count,
                             source_assets=parsed.asset_count, database_assets=parsed.asset_count,
                             source_relationships=parsed.relationship_count,
                             database_relationships=parsed.relationship_count, short_circuited=False),)

    monkeypatch.setattr(orchestrator, "import_parsed_exports", imported)
    summary = orchestrator.import_ios_backup_all(object(), backup.root)
    assert order[-2:] == ["voice_memos", "voicemail"]
    assert summary.audio_imported == summary.audio_files == 6 and summary.audio_failed == 0
    assert (summary.voice_memo_records, summary.voice_memo_transcripts) == (3, 1)
    assert (summary.voicemail_records, summary.voicemail_transcripts) == (1, 1)
    assert summary.exit_code == 0


def test_redelivered_voicemails_collapse_to_one_record(tmp_path: Path) -> None:
    b = Backup(tmp_path / "vm")
    audio = b"#!AMR\n" + b"\x02" * 20
    b.add("HomeDomain", "Library/Voicemail/1.amr", audio)
    b.add("HomeDomain", "Library/Voicemail/9.amr", audio)
    b.add("HomeDomain", "Library/Voicemail/9.transcript", _keyed_archive(
        {"transcriptionString": plistlib.UID(2), "confidence": plistlib.UID(3)}, ["Same message.", 0.9]))
    b.add_db("HomeDomain", "Library/Voicemail/voicemail.db", [
        (("CREATE TABLE voicemail (ROWID INTEGER PRIMARY KEY, date INTEGER, sender TEXT, callback_num TEXT, "
          "receiver TEXT, duration INTEGER, trashed_date INTEGER, flags INTEGER, uuid TEXT)"), ()),
        ("INSERT INTO voicemail VALUES (1, 1790000000, '8665459070', NULL, NULL, 40, 0, 0, 'OLD')", ()),
        ("INSERT INTO voicemail VALUES (9, 1790000000, '8665459070', NULL, NULL, 40, 0, 0, 'NEW')", ()),
    ])
    (record,) = voicemail_export(inspect_ios_backup(b.root)).records
    assert record.source_record_id == "NEW" and record.text == "Same message."
    assert record.metadata["duplicate_rowids"] == [1]
