"""Contacts from the phone's address book (`HomeDomain` `Library/AddressBook/AddressBook.sqlitedb`),
as `contact` records under source `ios_backup`.

Ry's rule is one copy of each item, the most complete. The Apple privacy export and the Google
export already hold contact cards (`records.record_type = 'contact'`), so a phone contact is
imported only when it adds something:

* it matches no existing card (by phone, email, or exact name), or
* it matches, but carries a phone number or email that none of the matching cards has.

Every other phone contact is a duplicate and is counted in the summary, not stored. The identity
bridge (`identity_contacts`) merges cards that share a phone or email, so a card imported here
joins its Apple/Google counterpart without any linking step.

The record's metadata `ios_contact` holds the structured card (names, phones, emails, addresses,
URLs, birthday, note, account); photos, wallpapers and Memoji are not copied.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..identity_normalize import normalize_email, normalize_name, phone_key
from ..models import NormalizedRecord, ParsedExport
from .inventory import IosBackupInventory, find_backup_files

SOURCE = "ios_backup"
NAMESPACE = "ios_backup/contacts/person"
PARSER_VERSION = "ios-contacts-v1"
ADDRESS_BOOK = "Library/AddressBook/AddressBook.sqlitedb"
APPLE_EPOCH = 978307200
PHONE, EMAIL, URL, ADDRESS, RELATED, SERVICE = 3, 4, 22, 5, 46, 16
_STRUCTURED = ("Street", "City", "State", "ZIP", "Country")


@dataclass
class DeviceContact:
    rowid: int
    guid: str
    first: str | None
    last: str | None
    display: str | None
    nickname: str | None
    organization: str | None
    note: str | None
    birthday: str | None
    created: datetime | None
    modified: datetime | None
    account: str
    phones: list[dict] = field(default_factory=list)
    emails: list[dict] = field(default_factory=list)
    urls: list[dict] = field(default_factory=list)
    addresses: list[dict] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        full = " ".join(p for p in (self.first, self.last) if p)
        return [n for n in dict.fromkeys([full, self.display or "", self.nickname or ""]) if n]

    @property
    def phone_keys(self) -> set[str]:
        return {k for k in (phone_key(p["value"]) for p in self.phones) if k}

    @property
    def email_keys(self) -> set[str]:
        return {normalize_email(e["value"]) for e in self.emails if "@" in e["value"]}


def _when(value: float | None) -> datetime | None:
    return datetime.fromtimestamp(value + APPLE_EPOCH, UTC) if value else None


def _label(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw.removeprefix("_$!<").removesuffix(">!$_") or None


def read_address_book(inventory: IosBackupInventory) -> list[DeviceContact]:
    found = find_backup_files(inventory.root, "HomeDomain", path=ADDRESS_BOOK)
    if not found:
        return []
    source = Path(next(iter(found.values())).path)
    with tempfile.TemporaryDirectory(prefix="pdl-ab-") as tmp:
        copy = Path(tmp) / "ab.db"
        shutil.copyfile(source, copy)
        db = sqlite3.connect(copy)
        try:
            return _read(db)
        finally:
            db.close()


def _read(db: sqlite3.Connection) -> list[DeviceContact]:
    stores = {row[0]: row[1] or "local" for row in db.execute("SELECT ROWID, Name FROM ABStore")}
    labels = dict(db.execute("SELECT ROWID, value FROM ABMultiValueLabel"))
    people: dict[int, DeviceContact] = {}
    for (rowid, guid, first, last, display, nickname, org, note, birthday, created, modified,
         store) in db.execute(
        "SELECT ROWID, guid, First, Last, DisplayName, Nickname, Organization, Note, Birthday, "
        "CreationDate, ModificationDate, StoreID FROM ABPerson ORDER BY ROWID"
    ):
        people[rowid] = DeviceContact(
            rowid=rowid, guid=guid or f"person-{rowid}", first=first, last=last,
            display=display or None, nickname=nickname, organization=org, note=note or None,
            birthday=birthday if birthday is None else str(birthday), created=_when(created),
            modified=_when(modified), account=stores.get(store, "local"),
        )
    entries: dict[int, dict[str, str]] = {}
    for parent, key, value in db.execute(
        "SELECT e.parent_id, k.value, e.value FROM ABMultiValueEntry e "
        "JOIN ABMultiValueEntryKey k ON k.ROWID = e.key"
    ):
        entries.setdefault(parent, {})[key] = value
    for uid, rid, prop, label, value in db.execute(
        "SELECT UID, record_id, property, label, value FROM ABMultiValue ORDER BY UID"
    ):
        person = people.get(rid)
        if person is None:
            continue
        item = {"label": _label(labels.get(label)), "value": value}
        if prop == PHONE and value:
            person.phones.append(item)
        elif prop == EMAIL and value:
            person.emails.append(item)
        elif prop == URL and value:
            person.urls.append(item)
        elif prop == ADDRESS:
            parts = entries.get(uid, {})
            person.addresses.append(
                {"label": item["label"], **{k: parts[k] for k in _STRUCTURED if parts.get(k)}}
            )
    return list(people.values())


# --- deciding what to import ----------------------------------------------------------------------


@dataclass(frozen=True)
class ExistingCard:
    record_id: int
    names: frozenset[str]
    phones: frozenset[str]
    emails: frozenset[str]


def load_existing_cards(connection: Connection) -> list[ExistingCard]:
    """Contact cards other sources already hold (Apple/Google), parsed the way the identity bridge
    parses them."""
    from ..identity_contacts import _parse_card

    cards = []
    for record_id, metadata in connection.execute(
        text("SELECT id, metadata FROM records WHERE record_type = 'contact' AND source <> :source"),
        {"source": SOURCE},
    ):
        card = _parse_card(record_id, json.loads(metadata) if isinstance(metadata, str) else metadata or {})
        if card is not None:
            cards.append(ExistingCard(
                record_id, frozenset(normalize_name(n) for n in card.names),
                frozenset(card.phones), frozenset(card.emails),
            ))
    return cards


def classify(contact: DeviceContact, cards: list[ExistingCard]) -> tuple[str, list[int]]:
    """('new' | 'more_complete' | 'duplicate', matching card record ids)."""
    phones, emails = contact.phone_keys, contact.email_keys
    names = {normalize_name(n) for n in contact.names}
    matches = [c for c in cards if (phones & c.phones) or (emails & c.emails) or (names & c.names)]
    if not matches:
        return "new", []
    known_phones = set().union(*(c.phones for c in matches))
    known_emails = set().union(*(c.emails for c in matches))
    extra = (phones - known_phones) or (emails - known_emails)
    return ("more_complete" if extra else "duplicate"), [c.record_id for c in matches]


def contact_export(
    contacts: list[tuple[DeviceContact, str, list[int]]],
    inventory: IosBackupInventory,
    *,
    sha256: str,
    path: str,
    size_bytes: int,
) -> ParsedExport:
    records = []
    for contact, status, matched in contacts:
        card = {
            k: v for k, v in {
                "names": contact.names,
                "given": contact.first,
                "family": contact.last,
                "nickname": contact.nickname,
                "organization": contact.organization,
                "note": contact.note,
                "birthday_raw": contact.birthday,
                "account": contact.account,
                "phones": contact.phones,
                "emails": contact.emails,
                "urls": contact.urls,
                "addresses": contact.addresses,
            }.items() if v
        }
        records.append(NormalizedRecord(
            source=SOURCE,
            record_type="contact",
            source_record_namespace=NAMESPACE,
            source_record_id=contact.guid,
            title=(contact.names or [None])[0],
            created_at=contact.created,
            updated_at=contact.modified,
            metadata={"ios_contact": card, "import_reason": status, "matched_card_record_ids": matched},
            authority_at=inventory.backup_date,
            authority_kind="exported_at" if inventory.backup_date else "unknown",
        ))
    return ParsedExport(
        source=SOURCE, parser_version=PARSER_VERSION, category="contacts",
        filename=Path(path).name, original_path=path, sha256=sha256, size_bytes=size_bytes,
        exported_at=inventory.backup_date,
        export_id=f"{inventory.unique_device_id}:{inventory.manifest_sha256[:16]}",
        snapshot_kind="partial", snapshot_scope="device_backup_contacts", records=tuple(records),
    )
