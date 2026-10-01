"""iPhone Messages from the device's own database (`sms.db`), as the gap-filling source
`ios_messages` beside the iMazing CSV source `imessages`.

The CSV export already holds most of the history and all of it is analysed, so the CSV stays the
copy of record for every message it has. The device database is read in full, every message is
matched to its CSV twin (same second, +-1 s, same direction; decided one-to-one in time order),
and only what the CSV lacks is imported:

* messages with no CSV twin (other services such as RCS, chats deleted before the export, and
  everything after the export date);
* photo captions the CSV dropped: the twin exists but its text is empty while the device has
  text. The device copy is imported as the more complete copy, carries `csv_twin` in its metadata
  and is linked to the CSV event; message counts skip it, since the CSV already counted it;
* reactions, edits and deletions of imported messages, plus reactions on CSV messages that the
  CSV does not show;
* group notices (item_type <> 0) with no CSV notification at that second.

Every matched CSV message also gets a `csv_message_on_device` relationship to its device chat,
carrying Apple's message GUID, service and attachment GUIDs.

More than one database can be read: the backup's `sms.db`, then older copies of the same
database (e.g. a `chat.db` taken earlier), which still hold messages since deleted on the phone.
Messages are keyed by Apple's GUID; the first store wins.

Text is the `text` column, or, when that is NULL (newer iOS), the string decoded from
`attributedBody` (a typedstream NSAttributedString). U+FFFC attachment placeholders are removed
from the text and counted in metadata. Handles lose the SMS-filter suffixes iOS appends
(`+1317...(smsft)`), so they match the CSV's sender IDs. The owner's own messages use the CSV
source's owner sentinel, `unknown`, so both sources agree.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import tempfile
from collections import defaultdict, deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

from sqlalchemy import text as sql
from sqlalchemy.engine import Connection

from ..fingerprints import canonical_json_bytes
from ..models import (
    NormalizedAsset,
    NormalizedConversation,
    NormalizedEvent,
    NormalizedRelationship,
    ParsedExport,
)

SOURCE = "ios_messages"
PARSER_VERSION = "ios-messages-v1"
CHAT_NAMESPACE = "sms_db/chat"
MESSAGE_NAMESPACE = "sms_db/message"
REACTION_NAMESPACE = "sms_db/reaction"
NOTIFICATION_NAMESPACE = "sms_db/notification"
EDIT_NAMESPACE = "sms_db/edit"
DELETION_NAMESPACE = "sms_db/deletion"
ATTACHMENT_NAMESPACE = "sms_db/attachment"
CSV_SOURCE = "imessages"
CSV_MESSAGE_NAMESPACE = "imazing_csv/message"
OWNER = "unknown"
SMS_DOMAIN = "HomeDomain"
SMS_DB = "Library/SMS/sms.db"
ATTACHMENT_DOMAIN = "MediaDomain"
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)
CHUNK_ITEMS = 2_000
TWIN_CHUNK = 5_000

_HANDLE_SUFFIX = re.compile(r"\(smsf\w*\)$")
_TAPBACKS = {0: "Loved", 1: "Liked", 2: "Disliked", 3: "Laughed", 4: "Emphasized", 5: "Questioned"}
_PLACEHOLDER = "￼"


class MessageStoreError(ValueError):
    """A message database could not be read safely."""


# --- reading a message database -----------------------------------------------------------------


def apple_time(value: int | None) -> datetime | None:
    """sms.db dates: nanoseconds since 2001 (seconds on very old rows); 0/NULL means none."""
    if not value:
        return None
    seconds = value / 1e9 if abs(value) > 1e11 else float(value)
    return APPLE_EPOCH + timedelta(seconds=seconds)


def decode_attributed_body(blob: bytes | None) -> str | None:
    """The plain string inside a typedstream NSAttributedString: after the `NSString` class name
    comes `+`, a length (one byte, or 0x81 + uint16 / 0x82 + uint32, little-endian), then UTF-8."""
    if not blob:
        return None
    start = blob.find(b"NSString")
    if start < 0:
        return None
    plus = blob.find(b"+", start + 8)
    if plus < 0 or plus - start > 16 or plus + 1 >= len(blob):
        return None
    length, offset = blob[plus + 1], plus + 2
    if length == 0x81:
        length, offset = int.from_bytes(blob[offset : offset + 2], "little"), offset + 2
    elif length == 0x82:
        length, offset = int.from_bytes(blob[offset : offset + 4], "little"), offset + 4
    elif length >= 0x80:
        return None
    try:
        return blob[offset : offset + length].decode("utf-8")
    except UnicodeDecodeError:
        return None


def normalize_handle(raw: str | None) -> str | None:
    if not raw:
        return None
    return _HANDLE_SUFFIX.sub("", raw.strip()) or None


def _clean(body: str | None) -> tuple[str | None, int]:
    if body is None:
        return None, 0
    placeholders = body.count(_PLACEHOLDER)
    cleaned = body.replace(_PLACEHOLDER, "").strip()
    return (cleaned or None), placeholders


@dataclass(frozen=True)
class StoreAttachment:
    guid: str
    filename: str | None
    mime_type: str | None
    uti: str | None
    transfer_name: str | None
    total_bytes: int
    created_at: datetime | None
    is_sticker: bool

    @property
    def relative_path(self) -> str | None:
        """The backup-relative path (MediaDomain) for `~/Library/SMS/Attachments/...`."""
        if not self.filename:
            return None
        name = self.filename
        for prefix in ("~/", "/var/mobile/"):
            if name.startswith(prefix):
                return name[len(prefix) :]
        return None


@dataclass(frozen=True)
class StoreMessage:
    guid: str
    store: str
    rowid: int
    at: datetime
    from_me: bool
    handle: str | None
    raw_handle: str | None
    service: str | None
    text: str | None
    text_origin: str | None
    placeholders: int
    subject: str | None
    item_type: int
    group_action_type: int
    associated_type: int
    associated_guid: str | None
    associated_emoji: str | None
    reply_to: str | None
    edited_at: datetime | None
    read_at: datetime | None
    delivered_at: datetime | None
    deleted_at: datetime | None
    chat_guid: str | None
    balloon: str | None
    is_audio: bool
    expressive: str | None
    attachments: tuple[StoreAttachment, ...]

    @property
    def kind(self) -> str:
        if 2000 <= self.associated_type < 4000:
            return "reaction"
        if self.item_type != 0:
            return "notification"
        return "message"

    @property
    def conversation_id(self) -> str:
        if self.chat_guid:
            return self.chat_guid
        return f"orphan:{self.handle}" if self.handle else "orphan:outgoing"

    @property
    def latest(self) -> datetime:
        return max(t for t in (self.at, self.edited_at, self.read_at, self.delivered_at, self.deleted_at) if t)

    @property
    def second(self) -> datetime:
        return self.at.replace(microsecond=0)


@dataclass(frozen=True)
class StoreChat:
    guid: str
    identifier: str | None
    display_name: str | None
    style: int | None
    service: str | None
    handles: tuple[str, ...]

    @property
    def is_group(self) -> bool:
        return self.style == 43


@dataclass
class MessageStore:
    label: str
    path: str
    sha256: str
    size_bytes: int
    exported_at: datetime | None
    chats: dict[str, StoreChat]
    messages: list[StoreMessage]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _columns(connection: sqlite3.Connection, table: str, wanted: Iterable[str]) -> str:
    present = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    return ", ".join(name if name in present else f"NULL AS {name}" for name in wanted)


_MESSAGE_COLUMNS = (
    "ROWID", "guid", "text", "attributedBody", "handle_id", "service", "subject", "date",
    "date_read", "date_delivered", "date_edited", "is_from_me", "item_type", "group_action_type",
    "associated_message_type", "associated_message_guid", "associated_message_emoji",
    "thread_originator_guid", "balloon_bundle_id", "is_audio_message", "expressive_send_style_id",
)
_ATTACHMENT_COLUMNS = (
    "ROWID", "guid", "filename", "mime_type", "uti", "transfer_name", "total_bytes",
    "created_date", "is_sticker",
)


def read_message_store(
    path: str | Path,
    label: str,
    *,
    sidecars: dict[str, Path] | None = None,
    exported_at: datetime | None = None,
) -> MessageStore:
    """Read one sms.db/chat.db. The database (and its -wal/-shm, passed as `sidecars` when they
    live under other names, as in a backup) is copied to a temporary directory first, so the
    original is never opened for writing and pending WAL content is included."""
    source = Path(path)
    if not source.is_file():
        raise MessageStoreError(f"{source} does not exist")
    sha256 = _sha256(source)
    with tempfile.TemporaryDirectory(prefix="pdl-sms-") as tmp:
        copy = Path(tmp) / "sms.db"
        shutil.copyfile(source, copy)
        for suffix in ("-wal", "-shm"):
            sidecar = (sidecars or {}).get(suffix) or source.with_name(source.name + suffix)
            if sidecar.is_file() and sidecar.stat().st_size:
                shutil.copyfile(sidecar, Path(tmp) / f"sms.db{suffix}")
        connection = sqlite3.connect(copy)
        try:
            chats, messages = _read(connection, label)
        except sqlite3.DatabaseError as error:
            raise MessageStoreError(f"{source} is not a readable Messages database: {error}") from error
        finally:
            connection.close()
    return MessageStore(
        label=label, path=str(source), sha256=sha256, size_bytes=source.stat().st_size,
        exported_at=exported_at, chats=chats, messages=messages,
    )


def _read(connection: sqlite3.Connection, label: str) -> tuple[dict[str, StoreChat], list[StoreMessage]]:
    handles = {rowid: raw for rowid, raw in connection.execute("SELECT ROWID, id FROM handle")}
    chat_handles: dict[int, list[str]] = defaultdict(list)
    for chat_id, handle_id in connection.execute(
        "SELECT chat_id, handle_id FROM chat_handle_join ORDER BY chat_id, handle_id"
    ):
        handle = normalize_handle(handles.get(handle_id))
        if handle and handle not in chat_handles[chat_id]:
            chat_handles[chat_id].append(handle)
    chats_by_rowid: dict[int, StoreChat] = {}
    for rowid, guid, identifier, display_name, style, service in connection.execute(
        "SELECT ROWID, guid, chat_identifier, display_name, style, service_name FROM chat"
    ):
        chats_by_rowid[rowid] = StoreChat(
            guid=guid, identifier=identifier, display_name=display_name or None, style=style,
            service=service, handles=tuple(chat_handles.get(rowid, ())),
        )
    message_chat: dict[int, int] = {}
    for chat_id, message_id in connection.execute(
        "SELECT chat_id, message_id FROM chat_message_join ORDER BY message_id, chat_id"
    ):
        message_chat.setdefault(message_id, chat_id)
    deleted: dict[int, tuple[int, int]] = {}
    for chat_id, message_id, delete_date in connection.execute(
        "SELECT chat_id, message_id, delete_date FROM chat_recoverable_message_join"
    ):
        deleted[message_id] = (chat_id, delete_date)
    attachments: dict[int, list[StoreAttachment]] = defaultdict(list)
    columns = _columns(connection, "attachment", _ATTACHMENT_COLUMNS)
    for row in connection.execute(
        f"SELECT j.message_id, {columns} FROM message_attachment_join j "
        "JOIN attachment ON attachment.ROWID = j.attachment_id ORDER BY j.message_id, attachment.ROWID"
    ):
        message_id, _rowid, guid, filename, mime, uti, transfer, total, created, sticker = row
        attachments[message_id].append(
            StoreAttachment(
                guid=guid, filename=filename, mime_type=mime, uti=uti, transfer_name=transfer,
                total_bytes=int(total or 0), created_at=apple_time(created), is_sticker=bool(sticker),
            )
        )

    messages: list[StoreMessage] = []
    columns = _columns(connection, "message", _MESSAGE_COLUMNS)
    for row in connection.execute(f"SELECT {columns} FROM message ORDER BY date, ROWID"):
        (rowid, guid, body, attributed, handle_id, service, subject, date, date_read, date_delivered,
         date_edited, from_me, item_type, action_type, assoc_type, assoc_guid, assoc_emoji,
         originator, balloon, is_audio, expressive) = row
        at = apple_time(date)
        if at is None or not guid:
            continue
        origin = "text" if body is not None else None
        if body is None:
            body = decode_attributed_body(attributed)
            origin = "attributedBody" if body is not None else None
        cleaned, placeholders = _clean(body)
        chat_rowid = message_chat.get(rowid)
        deleted_at = None
        if rowid in deleted:
            recoverable_chat, delete_date = deleted[rowid]
            chat_rowid = chat_rowid or recoverable_chat
            deleted_at = apple_time(delete_date)
        chat = chats_by_rowid.get(chat_rowid) if chat_rowid is not None else None
        raw_handle = handles.get(handle_id)
        messages.append(
            StoreMessage(
                guid=guid, store=label, rowid=rowid, at=at, from_me=bool(from_me),
                handle=normalize_handle(raw_handle), raw_handle=raw_handle, service=service,
                text=cleaned, text_origin=origin, placeholders=placeholders, subject=subject or None,
                item_type=int(item_type or 0), group_action_type=int(action_type or 0),
                associated_type=int(assoc_type or 0), associated_guid=_target_guid(assoc_guid),
                associated_emoji=assoc_emoji, reply_to=originator or None,
                edited_at=apple_time(date_edited), read_at=apple_time(date_read),
                delivered_at=apple_time(date_delivered), deleted_at=deleted_at,
                chat_guid=chat.guid if chat else None, balloon=balloon, is_audio=bool(is_audio),
                expressive=expressive, attachments=tuple(attachments.get(rowid, ())),
            )
        )
    chats = {chat.guid: chat for chat in chats_by_rowid.values()}
    return chats, messages


def _target_guid(value: str | None) -> str | None:
    """`p:0/GUID` (part 0 of GUID) or `bp:GUID` -> GUID."""
    if not value:
        return None
    if "/" in value:
        return value.split("/", 1)[1]
    return value.split(":", 1)[1] if ":" in value else value


def reaction_label(message: StoreMessage) -> str:
    if message.associated_emoji:
        return message.associated_emoji
    return _TAPBACKS.get(message.associated_type % 1000, f"reaction {message.associated_type}")


# --- the CSV source, as the matcher sees it -----------------------------------------------------


@dataclass(frozen=True)
class CsvMessage:
    source_record_id: str
    second: datetime
    outgoing: bool
    has_text: bool


@dataclass(frozen=True)
class CsvIndex:
    messages: tuple[CsvMessage, ...] = ()
    notification_seconds: tuple[datetime, ...] = ()
    reactions: tuple[tuple[str, datetime], ...] = ()  # (parent CSV message id, second)


def load_csv_index(connection: Connection) -> CsvIndex:
    rows = connection.execute(
        sql(
            "SELECT source_record_id, occurred_at, event_type, text, parent_source_node_id FROM events "
            "WHERE source = :source AND occurred_at IS NOT NULL AND event_type IN "
            "('message.incoming', 'message.outgoing', 'message.notification', 'message.reaction', "
            "'message.reaction_removed') ORDER BY id"
        ),
        {"source": CSV_SOURCE},
    ).all()
    messages, notifications, reactions = [], [], []
    for record_id, occurred_at, event_type, body, parent in rows:
        second = occurred_at.astimezone(UTC).replace(microsecond=0)
        if event_type in ("message.incoming", "message.outgoing"):
            messages.append(CsvMessage(record_id, second, event_type == "message.outgoing", _clean(body)[0] is not None))
        elif event_type == "message.notification":
            notifications.append(second)
        elif parent:
            reactions.append((parent, second))
    return CsvIndex(tuple(messages), tuple(notifications), tuple(reactions))


# --- deciding what to import --------------------------------------------------------------------

_NEARBY = (timedelta(0), timedelta(seconds=-1), timedelta(seconds=1))


@dataclass
class ImportPlan:
    stores: list[MessageStore]
    chats: dict[str, StoreChat]
    messages: list[StoreMessage]  # every message, all stores, GUID-unique, time order
    twins: dict[str, CsvMessage] = field(default_factory=dict)  # device GUID -> CSV message
    imported: list[StoreMessage] = field(default_factory=list)
    caption_twins: set[str] = field(default_factory=set)
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def imported_guids(self) -> set[str]:
        return {m.guid for m in self.imported}


def plan_import(stores: list[MessageStore], csv: CsvIndex) -> ImportPlan:
    by_guid: dict[str, StoreMessage] = {}
    chats: dict[str, StoreChat] = {}
    for store in stores:
        for guid, chat in store.chats.items():
            chats.setdefault(guid, chat)
        for message in store.messages:
            by_guid.setdefault(message.guid, message)
    messages = sorted(by_guid.values(), key=lambda m: (m.at, m.store != stores[0].label, m.rowid))
    plan = ImportPlan(stores=stores, chats=chats, messages=messages)

    pool: dict[tuple[datetime, bool], deque[CsvMessage]] = defaultdict(deque)
    for item in csv.messages:
        pool[(item.second, item.outgoing)].append(item)
    notification_pool: dict[datetime, int] = defaultdict(int)
    for second in csv.notification_seconds:
        notification_pool[second] += 1
    reaction_pool: dict[tuple[str, datetime], int] = defaultdict(int)
    for key in csv.reactions:
        reaction_pool[key] += 1

    def take(counter: dict, keys: Iterable) -> bool:
        for key in keys:
            if counter.get(key):
                counter[key] -= 1
                return True
        return False

    # Messages first (reactions need to know which CSV message their target became).
    for message in messages:
        if message.kind != "message":
            continue
        twin = None
        for delta in _NEARBY:
            queue = pool.get((message.second + delta, message.from_me))
            if queue:
                twin = queue.popleft()
                break
        if twin is None:
            plan.imported.append(message)
            continue
        plan.twins[message.guid] = twin
        if message.text and not twin.has_text:
            plan.caption_twins.add(message.guid)
            plan.imported.append(message)
        else:
            plan.skipped["message_in_csv"] += 1

    for message in messages:
        if message.kind == "notification":
            if take(notification_pool, (message.second + d for d in _NEARBY)):
                plan.skipped["notification_in_csv"] += 1
            else:
                plan.imported.append(message)
        elif message.kind == "reaction":
            twin = plan.twins.get(message.associated_guid or "")
            if twin is not None and message.associated_guid not in plan.caption_twins and take(
                reaction_pool, ((twin.source_record_id, message.second + d) for d in _NEARBY)
            ):
                plan.skipped["reaction_in_csv"] += 1
            else:
                plan.imported.append(message)
    plan.imported.sort(key=lambda m: (m.at, m.rowid))
    return plan


# --- normalization ------------------------------------------------------------------------------


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _relationship(
    relationship_type: str,
    subject: tuple[str, str, str, str],
    obj: tuple[str, str, str, str],
    *,
    authority_at: datetime,
    metadata: dict | None = None,
) -> NormalizedRelationship:
    identity = _digest({"type": relationship_type, "subject": list(subject), "object": list(obj)})
    return NormalizedRelationship(
        source=SOURCE,
        relationship_type=relationship_type,
        source_relationship_namespace=f"sms_db/{relationship_type}",
        source_relationship_id=f"relationship:{identity}",
        subject_kind=subject[0],  # type: ignore[arg-type]
        subject_source=subject[1],
        subject_namespace=subject[2],
        subject_source_id=subject[3],
        object_kind=obj[0],  # type: ignore[arg-type]
        object_source=obj[1],
        object_namespace=obj[2],
        object_source_id=obj[3],
        metadata=metadata or {},
        provider_updated_at=authority_at,
        authority_at=authority_at,
        authority_kind="provider_updated_at",
    )


def _event_ref(message: StoreMessage) -> tuple[str, str, str, str]:
    namespace = {"reaction": REACTION_NAMESPACE, "notification": NOTIFICATION_NAMESPACE}.get(
        message.kind, MESSAGE_NAMESPACE
    )
    return ("event", SOURCE, namespace, message.guid)


@dataclass(frozen=True)
class BackupAttachment:
    """An attachment file present in the backup: its path, and the asset that already holds it
    when the audio import took it (so it is linked, not stored twice)."""

    path: str
    size_bytes: int
    sha256: str | None = None
    existing: tuple[str, str, str] | None = None  # (source, namespace, source_asset_id)


@dataclass
class _Bundle:
    conversation_id: str
    events: list[NormalizedEvent] = field(default_factory=list)
    assets: list[NormalizedAsset] = field(default_factory=list)
    relationships: list[NormalizedRelationship] = field(default_factory=list)


def _target(plan: ImportPlan, guid: str | None, imported: set[str], by_guid: dict[str, StoreMessage]):
    """Where a reply/reaction target lives: an imported device message, a CSV message, or neither."""
    if not guid:
        return None
    if guid in imported:
        return _event_ref(by_guid[guid])
    twin = plan.twins.get(guid)
    if twin is not None:
        return ("event", CSV_SOURCE, CSV_MESSAGE_NAMESPACE, twin.source_record_id)
    return None


def _message_bundle(
    message: StoreMessage,
    plan: ImportPlan,
    imported: set[str],
    by_guid: dict[str, StoreMessage],
    backup_files: dict[str, BackupAttachment],
    emitted_assets: set[str],
) -> _Bundle:
    """One imported device message (or reaction/notice) as events, assets and relationships.
    `emitted_assets` holds attachment GUIDs already carried by an earlier bundle: one attachment
    can belong to several messages (a forwarded photo), but is one asset."""
    bundle = _Bundle(message.conversation_id)
    authority = message.latest
    kind = message.kind
    ref = _event_ref(message)
    twin = plan.twins.get(message.guid) if message.guid in plan.caption_twins else None
    metadata: dict[str, object] = {
        "guid": message.guid,
        "store": message.store,
        "service": message.service,
        "handle": message.raw_handle,
        "chat_guid": message.chat_guid,
        "text_origin": message.text_origin,
        "attachment_placeholders": message.placeholders or None,
        "subject": message.subject,
        "delivered_at": _iso(message.delivered_at),
        "read_at": _iso(message.read_at),
        "edited_at": _iso(message.edited_at),
        "deleted_at": _iso(message.deleted_at),
        "balloon": message.balloon,
        "audio_message": message.is_audio or None,
        "expressive_send_style": message.expressive,
        "reply_to_guid": message.reply_to,
    }
    if twin is not None:
        metadata["csv_twin"] = twin.source_record_id
    actor = OWNER if message.from_me else (message.handle or OWNER)
    parent = None
    if kind == "reaction":
        event_type = "message.reaction_removed" if message.associated_type >= 3000 else "message.reaction"
        body = reaction_label(message)
        metadata.update(target_guid=message.associated_guid, reaction_type=message.associated_type)
        target = _target(plan, message.associated_guid, imported, by_guid)
        if target is not None and target[1] == SOURCE:
            parent = message.associated_guid
    elif kind == "notification":
        event_type, body = "message.notification", message.text
        metadata.update(item_type=message.item_type, group_action_type=message.group_action_type)
        target = None
    else:
        event_type = "message.outgoing" if message.from_me else "message.incoming"
        body = message.text
        target = _target(plan, message.reply_to, imported, by_guid)
        if target is not None and target[1] == SOURCE:
            parent = message.reply_to
    bundle.events.append(
        NormalizedEvent(
            occurred_at=message.at,
            source=SOURCE,
            event_type=event_type,
            source_record_namespace=ref[2],
            actor_identity=actor,
            text=body,
            metadata={k: v for k, v in metadata.items() if v is not None},
            source_record_id=message.guid,
            source_node_id=message.guid,
            parent_source_node_id=parent,
            provider_updated_at=authority,
            authority_at=authority,
            authority_kind="provider_updated_at",
            lifecycle_state="provider_deleted" if message.deleted_at else "active",
        )
    )
    if target is not None:
        bundle.relationships.append(
            _relationship(
                "reaction_to_message" if kind == "reaction" else "message_replies_to",
                ref, target, authority_at=authority,
            )
        )
    if twin is not None:
        bundle.relationships.append(
            _relationship(
                "more_complete_copy_of", ref, ("event", CSV_SOURCE, CSV_MESSAGE_NAMESPACE, twin.source_record_id),
                authority_at=authority, metadata={"reason": "the CSV copy has no text; the device copy does"},
            )
        )
    for at, namespace, event_type, relationship_type in (
        (message.edited_at, EDIT_NAMESPACE, "message.edit", "edit_of_message"),
        (message.deleted_at, DELETION_NAMESPACE, "message.deletion", "deletion_of_message"),
    ):
        if at is None or kind != "message":
            continue
        lifecycle_id = f"{event_type.removeprefix('message.')}:{message.guid}"
        bundle.events.append(
            NormalizedEvent(
                occurred_at=at, source=SOURCE, event_type=event_type, source_record_namespace=namespace,
                actor_identity=actor, metadata={"guid": message.guid}, source_record_id=lifecycle_id,
                source_node_id=lifecycle_id, parent_source_node_id=message.guid, provider_updated_at=at,
                authority_at=at, authority_kind="provider_updated_at",
            )
        )
        bundle.relationships.append(
            _relationship(relationship_type, ("event", SOURCE, namespace, lifecycle_id), ref, authority_at=at)
        )
    for attachment in message.attachments:
        local = backup_files.get(attachment.relative_path or "")
        if local is not None and local.existing is not None:
            obj = ("asset", *local.existing)
        elif attachment.guid in emitted_assets:
            obj = ("asset", SOURCE, ATTACHMENT_NAMESPACE, attachment.guid)
        else:
            emitted_assets.add(attachment.guid)
            name = attachment.transfer_name or PurePosixPath(attachment.filename or "attachment").name
            bundle.assets.append(
                NormalizedAsset(
                    source=SOURCE,
                    asset_namespace=ATTACHMENT_NAMESPACE,
                    source_asset_id=attachment.guid,
                    sha256=local.sha256 if local else None,
                    mime_type=attachment.mime_type,
                    filename=name or "attachment",
                    original_path=local.path if local else (attachment.filename or name or attachment.guid),
                    size_bytes=local.size_bytes if local else 0,
                    captured_at=attachment.created_at or message.at,
                    metadata={
                        k: v for k, v in {
                            "guid": attachment.guid,
                            "device_path": attachment.filename,
                            "uti": attachment.uti,
                            "total_bytes": attachment.total_bytes,
                            "sticker": attachment.is_sticker or None,
                            "available_locally": local is not None,
                            "message_guid": message.guid,
                        }.items() if v is not None
                    },
                    provider_updated_at=authority,
                    authority_at=authority,
                    authority_kind="provider_updated_at",
                )
            )
            obj = ("asset", SOURCE, ATTACHMENT_NAMESPACE, attachment.guid)
        bundle.relationships.append(_relationship("message_has_attachment", ref, obj, authority_at=authority))
    return bundle


def _conversations(plan: ImportPlan) -> dict[str, NormalizedConversation]:
    """Every chat any device message belongs to (so twin links always have a target), with the
    span of all its device messages. Identical in every chunk that carries it."""
    spans: dict[str, list[datetime]] = {}
    handles: dict[str, set[str]] = defaultdict(set)
    counts: dict[str, int] = defaultdict(int)
    for message in plan.messages:
        key = message.conversation_id
        span = spans.setdefault(key, [message.at, message.latest])
        span[0], span[1] = min(span[0], message.at), max(span[1], message.latest)
        counts[key] += 1
        if message.handle and not message.from_me:
            handles[key].add(message.handle)
    conversations = {}
    for key, (started, ended) in spans.items():
        chat = plan.chats.get(key)
        participants = list(chat.handles) if chat and chat.handles else sorted(handles[key])
        if chat is not None:
            title = chat.display_name or " & ".join(participants) or chat.identifier
            kind = "group" if chat.is_group else "direct"
        else:
            title = f"Deleted chat with {key.removeprefix('orphan:')}" if key != "orphan:outgoing" else (
                "Sent messages from deleted chats (thread unknown)"
            )
            kind = "orphan"
        conversations[key] = NormalizedConversation(
            source=SOURCE,
            source_conversation_id=key,
            source_record_namespace=CHAT_NAMESPACE,
            title=title,
            started_at=started,
            ended_at=ended,
            metadata={k: v for k, v in {
                "chat_identifier": chat.identifier if chat else None,
                "display_name": chat.display_name if chat else None,
                "service": chat.service if chat else None,
                "kind": kind,
                "participants": participants,
                "device_message_count": counts[key],
            }.items() if v is not None},
            provider_updated_at=ended,
            authority_at=ended,
            authority_kind="provider_updated_at",
        )
    return conversations


def _twin_relationship(message: StoreMessage, twin: CsvMessage) -> NormalizedRelationship:
    return _relationship(
        "csv_message_on_device",
        ("event", CSV_SOURCE, CSV_MESSAGE_NAMESPACE, twin.source_record_id),
        ("conversation", SOURCE, CHAT_NAMESPACE, message.conversation_id),
        authority_at=message.latest,
        metadata={k: v for k, v in {
            "guid": message.guid,
            "store": message.store,
            "service": message.service,
            "handle": message.raw_handle,
            "device_time": message.at.isoformat(),
            "deleted_at": _iso(message.deleted_at),
            "attachment_guids": [a.guid for a in message.attachments] or None,
        }.items() if v is not None},
    )


def iter_message_exports(
    plan: ImportPlan,
    *,
    export_id: str,
    backup_files: dict[str, BackupAttachment] | None = None,
    chunk_items: int = CHUNK_ITEMS,
    twin_chunk: int = TWIN_CHUNK,
) -> Iterator[ParsedExport]:
    """Chunks per store: the store's imported items, then its CSV twin links. Every chat is carried
    by the first chunk of every store (same payload each time), and by any chunk with its events."""
    backup_files = backup_files or {}
    emitted_assets: set[str] = set()
    conversations = _conversations(plan)
    imported = plan.imported_guids
    by_guid = {m.guid: m for m in plan.messages}
    stores = {store.label: store for store in plan.stores}
    for label, store in stores.items():
        items = [m for m in plan.imported if m.store == label]
        twins = [(m, plan.twins[m.guid]) for m in plan.messages if m.store == label and m.guid in plan.twins]
        index = 0

        def export(
            bundles: list[_Bundle],
            relationships: list[NormalizedRelationship],
            first: bool,
            label: str = label,
            store: MessageStore = store,
        ) -> ParsedExport:
            nonlocal index
            index += 1
            events: dict[str, list[NormalizedEvent]] = defaultdict(list)
            for bundle in bundles:
                events[bundle.conversation_id].extend(bundle.events)
            keys = set(events) | (set(conversations) if first else set())
            return ParsedExport(
                source=SOURCE,
                parser_version=PARSER_VERSION,
                category=f"sms_db/{label}/{index:05d}",
                filename=Path(store.path).name,
                original_path=store.path,
                sha256=store.sha256,
                size_bytes=store.size_bytes,
                exported_at=store.exported_at,
                export_id=f"{export_id}:{label}",
                snapshot_kind="partial",
                snapshot_scope="device_messages",
                conversations=tuple(
                    conversations[k].model_copy(update={"events": tuple(events.get(k, ()))}) for k in sorted(keys)
                ),
                assets=tuple(a for b in bundles for a in b.assets),
                relationships=tuple(r for b in bundles for r in b.relationships) + tuple(relationships),
            )

        first = True
        for start in range(0, max(len(items), 1), chunk_items):
            bundles = [
                _message_bundle(m, plan, imported, by_guid, backup_files, emitted_assets)
                for m in items[start : start + chunk_items]
            ]
            yield export(bundles, [], first)
            first = False
        for start in range(0, len(twins), twin_chunk):
            yield export([], [_twin_relationship(m, t) for m, t in twins[start : start + twin_chunk]], False)
