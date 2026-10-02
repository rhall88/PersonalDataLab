"""Phone contacts: only cards that add something to the Apple/Google cards are imported, and the
identity bridge reads them like any other card."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from test_ios_backup import Backup

from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.identity_contacts import contact_proposals
from personal_data_lab.ios_backup.orchestrator import import_ios_contacts
from personal_data_lab.models import NormalizedEvent, NormalizedRecord, ParsedExport

pytestmark = pytest.mark.integration


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
            "TRUNCATE TABLE relationships, assets, records, events, conversations, identity_links, "
            "people, source_files, import_runs RESTART IDENTITY CASCADE"
        ))
        c.execute(text("TRUNCATE TABLE derive.derivation_run, derive.partition_state, "
                       "derive.evidence_epoch RESTART IDENTITY CASCADE"))
        c.execute(text("UPDATE derive.change_cursor SET last_import_run_id = 0"))
    yield


def _address_book(backup: Backup) -> None:
    stmts: list[tuple[str, tuple]] = [
        ("CREATE TABLE ABStore (ROWID INTEGER PRIMARY KEY, Name TEXT)", ()),
        ("CREATE TABLE ABMultiValueLabel (ROWID INTEGER PRIMARY KEY, value TEXT)", ()),
        (("CREATE TABLE ABPerson (ROWID INTEGER PRIMARY KEY, guid TEXT, First TEXT, Last TEXT, DisplayName TEXT, "
          "Nickname TEXT, Organization TEXT, Note TEXT, Birthday TEXT, CreationDate REAL, ModificationDate REAL, "
          "StoreID INTEGER)"), ()),
        ("CREATE TABLE ABMultiValue (UID INTEGER PRIMARY KEY, record_id INTEGER, property INTEGER, label INTEGER, value TEXT)", ()),
        ("CREATE TABLE ABMultiValueEntry (parent_id INTEGER, key INTEGER, value TEXT)", ()),
        ("CREATE TABLE ABMultiValueEntryKey (ROWID INTEGER PRIMARY KEY, value TEXT)", ()),
        ("INSERT INTO ABStore VALUES (0, 'local')", ()),
        ("INSERT INTO ABMultiValueLabel VALUES (1, '_$!<Mobile>!$_')", ()),
    ]
    people = [
        (1, "G1", "Sarah", None, "Sarah"),      # on the Apple card with the same number: duplicate
        (2, "G2", "Niteen", None, "Niteen"),    # unknown: new
        (3, "G3", "Daughter", None, "Daughter"),  # on the Apple card, plus a second number: more complete
    ]
    for rowid, guid, first, last, display in people:
        stmts.append(("INSERT INTO ABPerson VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, 758009195, 760864608, 0)",
                      (rowid, guid, first, last, display)))
    for uid, rid, prop, value in [(1, 1, 3, "(317) 555-0101"), (2, 2, 3, "(647) 488-9474"),
                                  (3, 3, 3, "(317) 555-0102"), (4, 3, 3, "(317) 555-0199"),
                                  (5, 2, 4, "niteen@example.com")]:
        stmts.append(("INSERT INTO ABMultiValue VALUES (?, ?, ?, 1, ?)", (uid, rid, prop, value)))
    backup.add_db("HomeDomain", "Library/AddressBook/AddressBook.sqlitedb", stmts)


def _apple_card(record_id: str, name: str, phone: str) -> NormalizedRecord:
    return NormalizedRecord(
        source="apple", record_type="contact", source_record_namespace="apple/contact", source_record_id=record_id,
        metadata={"apple": {"properties": {"FN": name, "TEL": phone}}},
    )


def test_only_contacts_that_add_something_are_imported(tmp_path: Path, database_engine: Engine) -> None:
    import_parsed_exports(database_engine, (ParsedExport(
        source="apple", parser_version="t", filename="c.vcf", original_path="c.vcf", sha256="a" * 64, size_bytes=1,
        records=(_apple_card("a1", "Sarah", "(317) 555-0101"), _apple_card("a2", "Daughter", "(317) 555-0102")),
    ),))
    backup = Backup(tmp_path / "backup")
    _address_book(backup)

    summary = import_ios_contacts(database_engine, backup.root)
    assert (summary.device_contacts, summary.new, summary.more_complete, summary.duplicates_skipped) == (3, 1, 1, 1)
    assert summary.imported == 2

    with database_engine.connect() as c:
        rows = {r.title: r for r in c.execute(text(
            "SELECT title, metadata FROM records WHERE source = 'ios_backup' AND record_type = 'contact'")).all()}
    assert set(rows) == {"Niteen", "Daughter"}
    assert rows["Niteen"].metadata["import_reason"] == "new"
    assert [p["value"] for p in rows["Daughter"].metadata["ios_contact"]["phones"]] == ["(317) 555-0102", "(317) 555-0199"]

    assert import_ios_contacts(database_engine, backup.root).short_circuited


def test_identity_bridge_matches_a_phone_contact_to_an_imessage_handle(tmp_path: Path, database_engine: Engine) -> None:
    import_parsed_exports(database_engine, (ParsedExport(
        source="imessages", parser_version="t", filename="m.csv", original_path="m.csv", sha256="b" * 64, size_bytes=1,
        events=(NormalizedEvent(occurred_at=None, source="imessages", event_type="message.incoming",
                                actor_identity="+16474889474", source_record_id="m1"),),
    ),))
    backup = Backup(tmp_path / "backup")
    _address_book(backup)
    import_ios_contacts(database_engine, backup.root)
    proposals = contact_proposals(database_engine)
    niteen = next(p for p in proposals if p.display_name == "Niteen")
    assert [(m.source, m.identity_value, m.match) for m in niteen.matches] == [("imessages", "+16474889474", "phone")]
