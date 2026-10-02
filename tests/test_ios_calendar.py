"""Calendar events from the phone: subscribed calendars and events the Apple/Google exports already
hold are skipped; the rest are imported with their attendees and organizer."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from test_ios_backup import Backup

from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.ios_backup.orchestrator import import_ios_calendar
from personal_data_lab.models import NormalizedEvent, ParsedExport

pytestmark = pytest.mark.integration
EPOCH = datetime(2001, 1, 1, tzinfo=UTC)


def _secs(at: datetime) -> float:
    return (at - EPOCH).total_seconds()


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
        c.execute(text("TRUNCATE TABLE relationships, assets, records, events, conversations, source_files, "
                       "import_runs RESTART IDENTITY CASCADE"))
        c.execute(text("TRUNCATE TABLE derive.derivation_run, derive.partition_state, "
                       "derive.evidence_epoch RESTART IDENTITY CASCADE"))
        c.execute(text("UPDATE derive.change_cursor SET last_import_run_id = 0"))
    yield


def _calendar(backup: Backup) -> None:
    standup = datetime(2026, 5, 4, 15, 0, tzinfo=UTC)
    dinner = datetime(2026, 5, 9, 23, 0, tzinfo=UTC)
    stmts: list[tuple[str, tuple]] = [
        ("CREATE TABLE Store (ROWID INTEGER PRIMARY KEY, name TEXT)", ()),
        ("CREATE TABLE Calendar (ROWID INTEGER PRIMARY KEY, store_id INTEGER, title TEXT)", ()),
        ("CREATE TABLE Location (ROWID INTEGER PRIMARY KEY, title TEXT)", ()),
        ("CREATE TABLE Identity (ROWID INTEGER PRIMARY KEY, display_name TEXT)", ()),
        (("CREATE TABLE Participant (ROWID INTEGER PRIMARY KEY, owner_id INTEGER, status INTEGER, role INTEGER, "
          "email TEXT, phone_number TEXT, is_self INTEGER, identity_id INTEGER)"), ()),
        (("CREATE TABLE CalendarItem (ROWID INTEGER PRIMARY KEY, UUID TEXT, summary TEXT, description TEXT, "
          "start_date REAL, end_date REAL, all_day INTEGER, start_tz TEXT, calendar_id INTEGER, location_id INTEGER, "
          "status INTEGER, has_recurrences INTEGER, url TEXT, creation_date REAL, last_modified REAL, "
          "organizer_id INTEGER, entity_type INTEGER)"), ()),
        ("INSERT INTO Store VALUES (1, 'iCloud')", ()), ("INSERT INTO Store VALUES (4, 'Subscribed Calendars')", ()),
        ("INSERT INTO Calendar VALUES (60, 1, 'Calendar')", ()), ("INSERT INTO Calendar VALUES (12, 4, 'US Holidays')", ()),
        ("INSERT INTO Location VALUES (1, 'Rocky Ripple Park')", ()),
        ("INSERT INTO Identity VALUES (1, 'Drobac, Michaela')", ()),
        ("INSERT INTO Participant VALUES (10, 3, 0, 0, 'michaela@example.com', NULL, 0, 1)", ()),
        ("INSERT INTO Participant VALUES (11, 3, 1, 0, 'me@example.com', NULL, 1, NULL)", ()),
        # 1: already in the Google export (same title, same day); 2: new, with attendees; 3: holiday
        ("INSERT INTO CalendarItem VALUES (1, 'U1', 'Team Standup', NULL, ?, ?, 0, 'GMT', 60, 0, 0, 0, NULL, ?, ?, 0, 2)",
         (_secs(standup), _secs(standup) + 1800, 700.0, 710.0)),
        (("INSERT INTO CalendarItem VALUES (3, 'U2', 'Dinner at the park', 'Bring chairs', ?, ?, 0, "
          "'America/Indiana/Indianapolis', 60, 1, 2, 1, NULL, ?, ?, 10, 2)"),
         (_secs(dinner), _secs(dinner) + 7200, 700.0, 710.0)),
        ("INSERT INTO CalendarItem VALUES (4, 'U3', 'Memorial Day', NULL, ?, ?, 1, '_float', 12, 0, 0, 0, NULL, 0, 0, 0, 2)",
         (_secs(datetime(2026, 5, 25, tzinfo=UTC)), _secs(datetime(2026, 5, 26, tzinfo=UTC)))),
    ]
    backup.add_db("HomeDomain", "Library/Calendar/Calendar.sqlitedb", stmts)


def test_only_events_the_exports_lack_are_imported(tmp_path: Path, database_engine: Engine) -> None:
    import_parsed_exports(database_engine, (ParsedExport(
        source="google", parser_version="t", filename="c.ics", original_path="c.ics", sha256="a" * 64, size_bytes=1,
        events=(NormalizedEvent(occurred_at=datetime(2026, 5, 4, 15, 0, tzinfo=UTC), source="google",
                                event_type="calendar_event", text="team  standup", source_record_id="g1"),),
    ),))
    backup = Backup(tmp_path / "backup")
    _calendar(backup)

    summary = import_ios_calendar(database_engine, backup.root)
    assert (summary.device_events, summary.subscribed_skipped, summary.duplicates_skipped, summary.imported) == (3, 1, 1, 1)

    with database_engine.connect() as c:
        row = c.execute(text("SELECT text, occurred_at, actor_identity, metadata FROM events "
                             "WHERE source = 'ios_backup' AND event_type = 'calendar_event'")).one()
    assert row.text == "Dinner at the park" and row.occurred_at == datetime(2026, 5, 9, 23, 0, tzinfo=UTC)
    assert row.actor_identity == "michaela@example.com"
    meta = row.metadata
    assert meta["organizer"]["name"] == "Drobac, Michaela" and meta["location"] == "Rocky Ripple Park"
    assert meta["attendees"] == [{"email": "me@example.com", "response": "accepted", "required": True, "self": True}]
    assert meta["recurring"] is True and meta["timezone"] == "America/Indiana/Indianapolis"

    assert import_ios_calendar(database_engine, backup.root).short_circuited
