"""iPhone Messages from the device backup's own sms.db (source `ios_messages`): decoding, matching
against the iMazing CSV source, the gap-filling import, and how the new messages reach text units,
interaction metrics and identity links."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from test_ios_backup import Backup

from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.derive import sync as derive_sync
from personal_data_lab.derive.derivers.interaction import _imessages_threads
from personal_data_lab.derive.derivers.semantic import semantic_text_unit
from personal_data_lab.identity import create_person, merge_identity
from personal_data_lab.ios_backup.messages import (
    APPLE_EPOCH,
    CsvIndex,
    CsvMessage,
    decode_attributed_body,
    iter_message_exports,
    normalize_handle,
    plan_import,
    read_message_store,
)
from personal_data_lab.ios_backup.orchestrator import import_ios_messages
from personal_data_lab.models import NormalizedConversation, NormalizedEvent, ParsedExport

T0 = datetime(2024, 5, 3, 15, 0, tzinfo=UTC)
SARAH = "+13175550101"


def _typedstream(body: str) -> bytes:
    data = body.encode()
    length = bytes([len(data)]) if len(data) < 0x80 else b"\x81" + len(data).to_bytes(2, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject"
            b"\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + length + data + b"\x86\x84\x02iI\x01")


def _ns(at: datetime) -> int:
    return int((at - APPLE_EPOCH).total_seconds() * 1e9)


class SmsDb:
    """A minimal sms.db with the tables and columns the reader uses."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.executescript(
            """
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
            CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT, display_name TEXT,
                               style INTEGER, service_name TEXT);
            CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
            CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
            CREATE TABLE chat_recoverable_message_join (chat_id INTEGER, message_id INTEGER, delete_date INTEGER);
            CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY, guid TEXT, filename TEXT, mime_type TEXT, uti TEXT,
                                     transfer_name TEXT, total_bytes INTEGER, created_date INTEGER, is_sticker INTEGER);
            CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
            CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, attributedBody BLOB,
                                  handle_id INTEGER, service TEXT, date INTEGER, is_from_me INTEGER,
                                  item_type INTEGER DEFAULT 0, associated_message_type INTEGER DEFAULT 0,
                                  associated_message_guid TEXT, date_edited INTEGER DEFAULT 0,
                                  thread_originator_guid TEXT);
            INSERT INTO handle VALUES (1, '+13175550101(smsft)');
            INSERT INTO chat VALUES (1, 'iMessage;-;+13175550101', '+13175550101', '', 45, 'iMessage');
            INSERT INTO chat_handle_join VALUES (1, 1);
            """
        )
        self.rowid = 0

    def message(self, guid: str, at: datetime, *, from_me: bool = False, body: str | None = None,
                attributed: str | None = None, chat: int | None = 1, reaction_to: str | None = None,
                reaction_type: int = 2000, deleted_at: datetime | None = None, edited_at: datetime | None = None,
                attachment: tuple[str, str] | None = None) -> None:
        self.rowid += 1
        self.db.execute(
            "INSERT INTO message (ROWID, guid, text, attributedBody, handle_id, service, date, is_from_me, "
            "associated_message_type, associated_message_guid, date_edited) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (self.rowid, guid, body, _typedstream(attributed) if attributed else None, 0 if from_me else 1,
             "iMessage", _ns(at), int(from_me), reaction_type if reaction_to else 0,
             f"p:0/{reaction_to}" if reaction_to else None, _ns(edited_at) if edited_at else 0),
        )
        if chat is not None and deleted_at is None:
            self.db.execute("INSERT INTO chat_message_join VALUES (?, ?)", (chat, self.rowid))
        if deleted_at is not None:
            self.db.execute("INSERT INTO chat_recoverable_message_join VALUES (?, ?, ?)", (chat, self.rowid, _ns(deleted_at)))
        if attachment is not None:
            att_guid, filename = attachment
            self.db.execute("INSERT INTO attachment (guid, filename, mime_type, transfer_name, total_bytes, is_sticker) "
                            "VALUES (?, ?, 'image/jpeg', ?, 1234, 0)", (att_guid, filename, filename.rsplit("/", 1)[-1]))
            att_rowid = self.db.execute("SELECT max(ROWID) FROM attachment").fetchone()[0]
            self.db.execute("INSERT INTO message_attachment_join VALUES (?, ?)", (self.rowid, att_rowid))

    def close(self) -> bytes:
        self.db.commit()
        self.db.close()
        return self.path.read_bytes()


def _device(tmp_path: Path) -> SmsDb:
    """Sarah's thread: two messages the CSV has (one a photo whose caption the CSV dropped), two it
    lacks (an RCS-era text whose words are only in attributedBody, and a later text), a reaction on a
    CSV message the CSV shows, one it does not, and a deleted message."""
    sms = SmsDb(tmp_path / "sms.db")
    sms.message("G-HAVE", T0, body="see you at six")
    sms.message("G-PHOTO", T0 + timedelta(minutes=1), from_me=True, body="￼I miss her",
                attachment=("AT-1", "~/Library/SMS/Attachments/aa/01/AT-1/IMG_1.jpeg"))
    sms.message("G-NEW", T0 + timedelta(minutes=5), attributed="only on the phone")
    sms.message("G-LATE", T0 + timedelta(minutes=9), from_me=True, body="later reply", edited_at=T0 + timedelta(minutes=10))
    sms.message("G-LOVE", T0 + timedelta(minutes=2), reaction_to="G-HAVE")
    sms.message("G-LAUGH", T0 + timedelta(minutes=3), reaction_to="G-HAVE", reaction_type=2003)
    sms.message("G-GONE", T0 + timedelta(minutes=7), body="never mind", deleted_at=T0 + timedelta(days=2))
    return sms


def _csv_index() -> CsvIndex:
    return CsvIndex(
        messages=(
            CsvMessage("csv-have", T0 + timedelta(seconds=1), False, True),
            CsvMessage("csv-photo", T0 + timedelta(minutes=1), True, False),
        ),
        reactions=(("csv-have", T0 + timedelta(minutes=2)),),
    )


def test_attributed_body_decodes_short_and_long_strings() -> None:
    assert decode_attributed_body(_typedstream("hello")) == "hello"
    long = "word " * 60
    assert decode_attributed_body(_typedstream(long)) == long
    assert decode_attributed_body(b"no class name here") is None
    assert normalize_handle("+13175550101(smsft)") == "+13175550101"
    assert normalize_handle("someone@example.com") == "someone@example.com"


def test_plan_imports_only_what_the_csv_lacks(tmp_path: Path) -> None:
    _device(tmp_path).close()
    store = read_message_store(tmp_path / "sms.db", "device")
    by_guid = {m.guid: m for m in store.messages}
    assert by_guid["G-NEW"].text == "only on the phone" and by_guid["G-NEW"].text_origin == "attributedBody"
    assert by_guid["G-PHOTO"].text == "I miss her" and by_guid["G-PHOTO"].placeholders == 1
    assert by_guid["G-NEW"].handle == SARAH and by_guid["G-GONE"].chat_guid == "iMessage;-;+13175550101"

    plan = plan_import([store], _csv_index())
    assert set(plan.twins) == {"G-HAVE", "G-PHOTO"}  # +-1 s, same direction
    assert plan.caption_twins == {"G-PHOTO"}
    assert [m.guid for m in plan.imported] == ["G-PHOTO", "G-LAUGH", "G-NEW", "G-GONE", "G-LATE"]
    assert dict(plan.skipped) == {"message_in_csv": 1, "reaction_in_csv": 1}


def test_older_snapshot_adds_only_messages_the_device_no_longer_has(tmp_path: Path) -> None:
    _device(tmp_path).close()
    (tmp_path / "old").mkdir()
    old = SmsDb(tmp_path / "old" / "chat.db")
    old.message("G-HAVE", T0, body="see you at six")
    old.message("G-DELETED-SINCE", T0 - timedelta(days=30), body="from April",
                attachment=("AT-OLD", "~/Library/SMS/Attachments/bb/02/AT-OLD/IMG_2.jpeg"))
    old.message("G-FORWARDED", T0 - timedelta(days=29), body="look again")
    old.db.execute("INSERT INTO message_attachment_join VALUES (?, 1)", (old.rowid,))  # same photo
    old.close()
    plan = plan_import(
        [read_message_store(tmp_path / "sms.db", "device"), read_message_store(tmp_path / "old" / "chat.db", "snapshot")],
        _csv_index(),
    )
    snapshot = [m.guid for m in plan.imported if m.store == "snapshot"]
    assert snapshot == ["G-DELETED-SINCE", "G-FORWARDED"]
    exports = list(iter_message_exports(plan, export_id="t"))
    assets = [a.source_asset_id for p in exports for a in p.assets]
    links = [r.object_source_id for p in exports for r in p.relationships if r.relationship_type == "message_has_attachment"]
    assert assets.count("AT-OLD") == 1 and links.count("AT-OLD") == 2  # one asset, both messages linked


# --- against a database --------------------------------------------------------------------------


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


@pytest.fixture
def clean(database_engine: Engine) -> Iterator[None]:
    with database_engine.begin() as c:
        c.execute(text(
            "TRUNCATE TABLE semantic.text_unit, relationships, assets, records, events, conversations, "
            "identity_links, people, source_files, import_runs RESTART IDENTITY CASCADE"
        ))
        c.execute(text("TRUNCATE TABLE derive.derivation_run, derive.partition_state, "
                       "derive.evidence_epoch RESTART IDENTITY CASCADE"))
        c.execute(text("UPDATE derive.change_cursor SET last_import_run_id = 0"))
    yield


def _csv_event(record_id: str, at: datetime, event_type: str, body: str | None, actor: str,
               parent: str | None = None) -> NormalizedEvent:
    return NormalizedEvent(
        occurred_at=at, source="imessages", event_type=event_type, actor_identity=actor, text=body,
        source_record_namespace="imazing_csv/reaction" if parent else "imazing_csv/message",
        source_record_id=record_id, source_node_id=record_id, parent_source_node_id=parent,
    )


def _import_csv(engine: Engine) -> None:
    import_parsed_exports(engine, (ParsedExport(
        source="imessages", parser_version="test-csv", filename="Sarah.csv", original_path="Sarah.csv",
        sha256="c" * 64, size_bytes=1,
        conversations=(NormalizedConversation(
            source="imessages", source_conversation_id="conversation:sarah", title="Sarah",
            source_record_namespace="imazing_csv/conversation",
            events=(
                _csv_event("csv-have", T0 + timedelta(seconds=1), "message.incoming", "see you at six", SARAH),
                _csv_event("csv-photo", T0 + timedelta(minutes=1), "message.outgoing", None, "unknown"),
                _csv_event("csv-love", T0 + timedelta(minutes=2), "message.reaction", "Loved", "Sarah", "csv-have"),
            ),
        ),),
    ),))


def test_import_fills_gaps_links_twins_and_mirrors_identity(tmp_path: Path, database_engine: Engine, clean) -> None:
    _import_csv(database_engine)
    sarah = create_person(database_engine, "Sarah")
    merge_identity(database_engine, sarah.id, "imessages", SARAH)
    backup = Backup(tmp_path / "backup")
    backup.add("HomeDomain", "Library/SMS/sms.db", _device(tmp_path).close())
    backup.add("MediaDomain", "Library/SMS/Attachments/aa/01/AT-1/IMG_1.jpeg", b"\xff\xd8jpeg")

    summary = import_ios_messages(database_engine, backup.root)
    assert summary.matched_to_csv == 2 and summary.caption_twins == 1
    assert summary.imported == {"message": 4, "reaction": 1}
    assert summary.attachments_local == 1 and summary.identity_links_mirrored == 1

    with database_engine.connect() as c:
        events = {r.source_record_id: r for r in c.execute(text(
            "SELECT source_record_id, event_type, text, lifecycle_state, actor_identity, actor_person_id, metadata "
            "FROM events WHERE source = 'ios_messages'")).all()}
        assert events["G-NEW"].text == "only on the phone" and events["G-NEW"].actor_person_id == sarah.id
        assert events["G-PHOTO"].metadata["csv_twin"] == "csv-photo" and events["G-PHOTO"].actor_identity == "unknown"
        assert events["G-GONE"].lifecycle_state == "provider_deleted"
        assert {"edit:G-LATE", "deletion:G-GONE", "G-LAUGH"} <= set(events)
        relationships = dict(c.execute(text(
            "SELECT relationship_type, count(*) FROM relationships WHERE source = 'ios_messages' GROUP BY 1")).all())
        assert relationships["csv_message_on_device"] == 2
        assert relationships["more_complete_copy_of"] == 1
        assert relationships["reaction_to_message"] == 1
        sha = c.execute(text("SELECT sha256 FROM assets WHERE source = 'ios_messages'")).scalar_one()
        assert sha is not None

    again = import_ios_messages(database_engine, backup.root)
    assert again.chunks_short_circuited == again.chunks


def test_new_messages_reach_text_units_and_interaction_once(tmp_path: Path, database_engine: Engine, clean) -> None:
    _import_csv(database_engine)
    backup = Backup(tmp_path / "backup")
    backup.add("HomeDomain", "Library/SMS/sms.db", _device(tmp_path).close())
    import_ios_messages(database_engine, backup.root)
    derive_sync(database_engine)
    with database_engine.connect() as c:
        epoch = c.execute(text("SELECT max(id) FROM derive.evidence_epoch")).scalar_one()
        units = semantic_text_unit(c, "ios_messages:2024-05", epoch).rows
        threads = _imessages_threads(c)
    assert len(units) == 1
    assert units[0]["text"] == "I miss her\n\nonly on the phone\n\nnever mind\n\nlater reply"
    # CSV: 2 messages; the device adds 2 live ones (its caption copy is the CSV message itself, and
    # the deleted message is not active).
    assert len(threads[("imessages", SARAH)]) == 4
