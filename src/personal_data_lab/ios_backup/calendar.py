"""Calendar events from the phone (`HomeDomain` `Library/Calendar/Calendar.sqlitedb`) as
`calendar_event` events under source `ios_backup`.

One copy of each item (owner's rule): the Apple and Google exports already hold calendar events, so a
phone event is imported only when no existing `calendar_event` has the same title on the same day
(within a day, since all-day events are stored as floating dates). Subscribed calendars (US
Holidays, Facebook Birthdays) are not the owner's data and are skipped; they are counted in the
summary. A recurring event is one row, as the phone stores it.

The event text is the title; description, location, organizer, attendees (with response status),
recurrence, status and the calendar it lives in are metadata. Event times are the phone's absolute
instants (seconds since 2001); an all-day event is its floating date at 00:00 UTC.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..models import NormalizedEvent, ParsedExport
from .inventory import IosBackupInventory, find_backup_files

SOURCE = "ios_backup"
NAMESPACE = "ios_backup/calendar/event"
PARSER_VERSION = "ios-calendar-v1"
CALENDAR_DB = "Library/Calendar/Calendar.sqlitedb"
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)
_SUBSCRIBED_STORE = "Subscribed Calendars"
_ENTITY_EVENT = 2
_RESPONSES = {0: "none", 1: "accepted", 2: "declined", 3: "tentative", 4: "delegated"}


@dataclass
class DeviceEvent:
    uuid: str
    summary: str | None
    description: str | None
    start: datetime | None
    end: datetime | None
    all_day: bool
    timezone: str | None
    calendar: str | None
    store: str | None
    location: str | None
    status: int | None
    recurring: bool
    url: str | None
    created: datetime | None
    modified: datetime | None
    organizer: dict | None = None
    attendees: list[dict] = field(default_factory=list)

    @property
    def title_key(self) -> str:
        return " ".join((self.summary or "").lower().split())


def _when(value: float | None) -> datetime | None:
    if value is None or value < -1e10:  # "no date" sentinel
        return None
    return APPLE_EPOCH + timedelta(seconds=value)


def read_calendar(inventory: IosBackupInventory) -> list[DeviceEvent]:
    found = find_backup_files(inventory.root, "HomeDomain", path=CALENDAR_DB)
    if not found:
        return []
    with tempfile.TemporaryDirectory(prefix="pdl-cal-") as tmp:
        copy = Path(tmp) / "c.db"
        shutil.copyfile(next(iter(found.values())).path, copy)
        db = sqlite3.connect(copy)
        try:
            return _read(db)
        finally:
            db.close()


def _read(db: sqlite3.Connection) -> list[DeviceEvent]:
    participants: dict[int, list[dict]] = {}
    people: dict[int, dict] = {}
    for pk, owner, status, role, email, phone, is_self, name in db.execute(
        "SELECT p.ROWID, p.owner_id, p.status, p.role, p.email, p.phone_number, p.is_self, i.display_name "
        "FROM Participant p LEFT JOIN Identity i ON i.ROWID = p.identity_id ORDER BY p.ROWID"
    ):
        person = {k: v for k, v in {
            "name": name, "email": email, "phone": phone,
            "response": _RESPONSES.get(status, str(status)) if status is not None else None,
            "required": role == 0 or None, "self": bool(is_self) or None,
        }.items() if v is not None}
        people[pk] = person
        participants.setdefault(owner, []).append(person)
    events = []
    for (uuid, rowid, summary, description, start, end, all_day, tz, calendar, store, location, status,
         recurring, url, created, modified, organizer) in db.execute(
        "SELECT i.UUID, i.ROWID, i.summary, i.description, i.start_date, i.end_date, i.all_day, i.start_tz, "
        "c.title, s.name, l.title, i.status, i.has_recurrences, i.url, i.creation_date, i.last_modified, "
        "i.organizer_id FROM CalendarItem i JOIN Calendar c ON c.ROWID = i.calendar_id "
        "LEFT JOIN Store s ON s.ROWID = c.store_id LEFT JOIN Location l ON l.ROWID = i.location_id "
        f"WHERE i.entity_type = {_ENTITY_EVENT} ORDER BY i.ROWID"
    ):
        events.append(DeviceEvent(
            uuid=uuid or f"item-{rowid}", summary=summary, description=description or None,
            start=_when(start), end=_when(end), all_day=bool(all_day),
            timezone=None if tz in (None, "_float") else tz, calendar=calendar, store=store,
            location=location, status=status, recurring=bool(recurring), url=url or None,
            created=_when(created), modified=_when(modified), organizer=people.get(organizer),
            attendees=[p for p in participants.get(rowid, []) if p is not people.get(organizer)],
        ))
    return events


def load_existing(connection: Connection) -> list[tuple[str, date]]:
    rows = connection.execute(text(
        "SELECT text, occurred_at FROM events WHERE event_type = 'calendar_event' AND source <> :source "
        "AND occurred_at IS NOT NULL"), {"source": SOURCE}).all()
    return [(" ".join((t or "").lower().split()), at.astimezone(UTC).date()) for t, at in rows]


def classify(event: DeviceEvent, existing: set[tuple[str, date]]) -> str:
    """'subscribed' | 'duplicate' | 'new'."""
    if event.store == _SUBSCRIBED_STORE:
        return "subscribed"
    if event.start is not None:
        day = event.start.date()
        if any((event.title_key, day + timedelta(days=d)) in existing for d in (-1, 0, 1)):
            return "duplicate"
    return "new"


def calendar_export(
    events: list[DeviceEvent], inventory: IosBackupInventory, *, sha256: str, path: str, size_bytes: int
) -> ParsedExport:
    stamp = inventory.backup_date
    out = []
    for event in events:
        out.append(NormalizedEvent(
            occurred_at=event.start,
            source=SOURCE,
            event_type="calendar_event",
            source_record_namespace=NAMESPACE,
            actor_identity=(event.organizer or {}).get("email"),
            text=event.summary,
            metadata={k: v for k, v in {
                "end": event.end.isoformat() if event.end else None,
                "all_day": event.all_day or None, "timezone": event.timezone,
                "calendar": event.calendar, "store": event.store, "location": event.location,
                "description": event.description, "status": event.status, "recurring": event.recurring or None,
                "url": event.url, "organizer": event.organizer, "attendees": event.attendees or None,
                "created": event.created.isoformat() if event.created else None,
                "modified": event.modified.isoformat() if event.modified else None,
            }.items() if v is not None},
            source_record_id=event.uuid,
            source_node_id=event.uuid,
            provider_updated_at=event.modified,
            authority_at=stamp,
            authority_kind="exported_at" if stamp else "unknown",
        ))
    return ParsedExport(
        source=SOURCE, parser_version=PARSER_VERSION, category="calendar", filename=Path(path).name,
        original_path=path, sha256=sha256, size_bytes=size_bytes, exported_at=stamp,
        export_id=f"{inventory.unique_device_id}:{inventory.manifest_sha256[:16]}",
        snapshot_kind="partial", snapshot_scope="device_backup_calendar", events=tuple(out),
    )
