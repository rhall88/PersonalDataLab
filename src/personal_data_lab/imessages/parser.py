from __future__ import annotations

import csv
import hashlib
import mimetypes
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..fingerprints import canonical_json_bytes
from ..models import (
    NormalizedAsset,
    NormalizedConversation,
    NormalizedEvent,
    NormalizedRecord,
    NormalizedRelationship,
    ParsedExport,
)
from .inventory import IMessageInventory, IMessageSourceFile

SOURCE = "imessages"
PARSER_VERSION = "imazing-messages-csv-v2"
CSV_BATCH_ROWS = 2_000
CONVERSATION_NAMESPACE = "imazing_csv/conversation"
MESSAGE_NAMESPACE = "imazing_csv/message"
PARTICIPANT_NAMESPACE = "imazing_csv/participant"
ATTACHMENT_NAMESPACE = "imazing_csv/attachment"

_REPLY_PATTERN = re.compile(
    r"^↩\s*Replying to (?P<actor>.*?),\s*"
    r"(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}):\s*(?P<preview>.*)$",
    re.DOTALL,
)
_REACTION_PATTERN = re.compile(
    r"^(?P<actor>.+?) (?P<action>reacted with|removed the reaction) "
    r"(?P<reaction>.+?) on "
    r"(?P<timestamp>\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} [AP]M)$",
    re.DOTALL,
)


class IMessageAdapterError(ValueError):
    """Raised when a classified Messages CSV cannot be represented safely."""


@dataclass
class ParticipantProfile:
    identity: str
    source_id: str
    display_name: str | None
    latest_at: datetime


@dataclass
class ConversationProfile:
    source_id: str
    title: str
    started_at: datetime
    ended_at: datetime
    message_count: int = 0
    participant_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class MessageLocator:
    source_id: str
    direction: str
    sender_id: str
    sender_name: str
    text: str
    attachment: str
    attachment_type: str


@dataclass
class FileProfile:
    conversations: dict[str, ConversationProfile]
    participants: dict[str, ParticipantProfile]
    reply_targets: dict[tuple[str, str], list[MessageLocator]]


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _conversation_id(chat_session: str) -> str:
    return f"conversation:{_digest({'chat_session': chat_session})}"


def _participant_identity(row: dict[str, str]) -> str:
    sender_id = row["Sender ID"].strip()
    sender_name = row["Sender Name"].strip()
    return sender_id or (f"name:{sender_name}" if sender_name else "unknown")


def _participant_id(identity: str) -> str:
    return f"participant:{_digest({'identity': identity.casefold()})}"


def _message_base(row: dict[str, str]) -> tuple[str, ...]:
    return (
        row["Chat Session"],
        row["Message Date"],
        _participant_identity(row),
        row["Type"],
        row["Service"],
    )


def _message_id(row: dict[str, str], occurrence: int) -> str:
    return f"message:{_digest({'base': _message_base(row), 'occurrence': occurrence})}"


def _metadata(*, semantic_kind: str, timezone_name: str, **imazing: object) -> dict[str, object]:
    return {
        "imazing": imazing,
        "_pdl": {
            "logical_provider": "apple",
            "source_family": "imessages_device_export",
            "acquisition_source": "iphone_backup_or_device",
            "acquisition_method": "imazing_csv_export",
            "semantic_kind": semantic_kind,
            "source_timezone": timezone_name,
        },
    }


def _timezone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise IMessageAdapterError("unknown IANA timezone for message timestamps") from exc


def _timestamp(value: str, timezone: ZoneInfo, *, reaction: bool = False) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    pattern = "%m/%d/%Y %I:%M:%S %p" if reaction else "%Y-%m-%d %H:%M:%S"
    try:
        return datetime.strptime(value, pattern).replace(tzinfo=timezone)
    except ValueError as exc:
        raise IMessageAdapterError("message export contains an invalid timestamp") from exc


def _rows(member: IMessageSourceFile) -> Iterator[dict[str, str]]:
    delimiters = {"comma": ",", "semicolon": ";", "tab": "\t"}
    delimiter = delimiters.get(member.delimiter or "")
    if delimiter is None:
        raise IMessageAdapterError("classified message CSV has no delimiter")
    try:
        with Path(member.source_path).open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise IMessageAdapterError("message export contains a malformed CSV row")
                if not row["Chat Session"].strip() or not row["Message Date"].strip():
                    raise IMessageAdapterError("message export row lacks conversation or timestamp")
                yield row
    except (OSError, UnicodeError, csv.Error) as exc:
        raise IMessageAdapterError("message CSV could not be parsed safely") from exc


def _reply(value: str) -> tuple[str, str, str] | None:
    match = _REPLY_PATTERN.fullmatch(value.strip())
    if match is None:
        return None
    return (
        match.group("actor").strip(),
        match.group("timestamp").strip(),
        match.group("preview").strip(),
    )


def _preview(value: str) -> str:
    result = value.strip()
    if result.startswith("«") and result.endswith("»"):
        result = result[1:-1].strip()
    return re.sub(r"\s+", " ", result).casefold()


def _preview_matches(locator: MessageLocator, preview: str) -> bool:
    wanted = _preview(preview)
    if not wanted:
        return False
    truncated = wanted.endswith(("...", "…"))
    wanted = wanted.removesuffix("...").removesuffix("…").rstrip()
    candidates = {
        re.sub(r"\s+", " ", locator.text.strip()).casefold(),
        f"({locator.attachment_type.strip().casefold()})",
        PurePosixPath(locator.attachment.replace("\\", "/")).name.casefold(),
    }
    if truncated:
        return any(candidate.startswith(wanted) for candidate in candidates)
    return wanted in candidates


def _resolve_reply(
    row: dict[str, str], profile: FileProfile
) -> tuple[str | None, dict[str, object]]:
    parsed = _reply(row["Replying to"])
    if parsed is None:
        return None, {"resolution": "syntax_unparsed", "raw": row["Replying to"]}
    actor, timestamp, preview = parsed
    candidates = list(profile.reply_targets.get((row["Chat Session"], timestamp), ()))
    actor_folded = actor.casefold()
    actor_candidates = [
        item
        for item in candidates
        if (
            (actor_folded == "you" and item.direction.casefold() == "outgoing")
            or actor_folded in {item.sender_id.casefold(), item.sender_name.casefold()}
        )
    ]
    preview_candidates = [item for item in candidates if _preview_matches(item, preview)]
    intersection = [item for item in actor_candidates if item in preview_candidates]
    for selection, method in (
        (intersection, "actor_timestamp_preview"),
        (actor_candidates, "actor_timestamp"),
        (preview_candidates, "timestamp_preview"),
        (candidates, "timestamp"),
    ):
        if len(selection) == 1:
            return selection[0].source_id, {
                "resolution": "resolved",
                "method": method,
                "reply_actor": actor,
                "reply_timestamp": timestamp,
                "reply_preview": preview,
            }
    return None, {
        "resolution": "unresolved",
        "candidate_count": len(candidates),
        "reply_actor": actor,
        "reply_timestamp": timestamp,
        "reply_preview": preview,
        "raw": row["Replying to"],
    }


def _reaction_parts(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.splitlines() if item.strip())


def _reaction(
    value: str, timezone: ZoneInfo
) -> tuple[str | None, str, datetime | None, str | None]:
    match = _REACTION_PATTERN.fullmatch(value.strip())
    if match is None:
        raise IMessageAdapterError("reaction entry has an unsupported structure")
    return (
        match.group("actor").strip(),
        match.group("reaction").strip(),
        _timestamp(match.group("timestamp"), timezone, reaction=True),
        "removed" if match.group("action") == "removed the reaction" else "added",
    )


def _latest(values: Iterator[datetime | None]) -> datetime:
    present = [value for value in values if value is not None]
    if not present:
        raise IMessageAdapterError("message row has no usable authority timestamp")
    return max(present)


def _authority(row: dict[str, str], timezone: ZoneInfo) -> datetime:
    values = [
        _timestamp(row[field], timezone)
        for field in (
            "Message Date",
            "Delivered Date",
            "Read Date",
            "Edited Date",
            "Deleted Date",
        )
    ]
    values.extend(_reaction(item, timezone)[2] for item in _reaction_parts(row["Reactions"]))
    return _latest(iter(values))


def _profile(member: IMessageSourceFile, timezone: ZoneInfo) -> FileProfile:
    conversations: dict[str, ConversationProfile] = {}
    participants: dict[str, ParticipantProfile] = {}
    target_keys: set[tuple[str, str]] = set()
    for row in _rows(member):
        occurred_at = _timestamp(row["Message Date"], timezone)
        if occurred_at is None:
            raise IMessageAdapterError("message row has no timestamp")
        conversation_id = _conversation_id(row["Chat Session"])
        conversation = conversations.get(conversation_id)
        if conversation is None:
            conversation = ConversationProfile(
                source_id=conversation_id,
                title=row["Chat Session"],
                started_at=occurred_at,
                ended_at=occurred_at,
            )
            conversations[conversation_id] = conversation
        conversation.started_at = min(conversation.started_at, occurred_at)
        conversation.ended_at = max(conversation.ended_at, _authority(row, timezone))
        conversation.message_count += 1
        identity = _participant_identity(row)
        participant_id = _participant_id(identity)
        conversation.participant_ids.add(participant_id)
        display_name = row["Sender Name"].strip() or None
        existing = participants.get(participant_id)
        if existing is None or occurred_at >= existing.latest_at:
            participants[participant_id] = ParticipantProfile(
                identity=identity,
                source_id=participant_id,
                display_name=display_name,
                latest_at=occurred_at,
            )
        parsed_reply = _reply(row["Replying to"])
        if parsed_reply is not None:
            target_keys.add((row["Chat Session"], parsed_reply[1]))

    reply_targets: dict[tuple[str, str], list[MessageLocator]] = defaultdict(list)
    occurrences: Counter[tuple[str, ...]] = Counter()
    for row in _rows(member):
        base = _message_base(row)
        occurrences[base] += 1
        key = (row["Chat Session"], row["Message Date"])
        if key in target_keys:
            reply_targets[key].append(
                MessageLocator(
                    source_id=_message_id(row, occurrences[base]),
                    direction=row["Type"],
                    sender_id=row["Sender ID"].strip(),
                    sender_name=row["Sender Name"].strip(),
                    text=row["Text"],
                    attachment=row["Attachment"],
                    attachment_type=row["Attachment type"],
                )
            )
    return FileProfile(conversations, participants, dict(reply_targets))


def _participant_record(profile: ParticipantProfile, timezone_name: str) -> NormalizedRecord:
    return NormalizedRecord(
        source=SOURCE,
        record_type="message_participant",
        source_record_namespace=PARTICIPANT_NAMESPACE,
        source_record_id=profile.source_id,
        title=profile.display_name,
        actor_identity=profile.identity,
        metadata=_metadata(
            semantic_kind="participant",
            timezone_name=timezone_name,
            sender_identity=profile.identity,
            sender_name=profile.display_name,
        ),
        provider_updated_at=profile.latest_at,
        authority_at=profile.latest_at,
        authority_kind="provider_updated_at",
    )


def _relationship(
    relationship_type: str,
    subject_kind: str,
    subject_namespace: str,
    subject_id: str,
    object_kind: str,
    object_namespace: str,
    object_id: str,
    *,
    authority_at: datetime,
    metadata: dict[str, object],
) -> NormalizedRelationship:
    identity = _digest(
        {
            "type": relationship_type,
            "subject": [subject_kind, subject_namespace, subject_id],
            "object": [object_kind, object_namespace, object_id],
        }
    )
    return NormalizedRelationship(
        source=SOURCE,
        relationship_type=relationship_type,
        source_relationship_namespace=f"imazing_csv/{relationship_type}",
        source_relationship_id=f"relationship:{identity}",
        subject_kind=subject_kind,  # type: ignore[arg-type]
        subject_source=SOURCE,
        subject_namespace=subject_namespace,
        subject_source_id=subject_id,
        object_kind=object_kind,  # type: ignore[arg-type]
        object_source=SOURCE,
        object_namespace=object_namespace,
        object_source_id=object_id,
        metadata=metadata,
        provider_updated_at=authority_at,
        authority_at=authority_at,
        authority_kind="provider_updated_at",
    )


def _conversation(
    profile: ConversationProfile,
    timezone_name: str,
    events: tuple[NormalizedEvent, ...],
) -> NormalizedConversation:
    return NormalizedConversation(
        source=SOURCE,
        source_conversation_id=profile.source_id,
        source_record_namespace=CONVERSATION_NAMESPACE,
        title=profile.title,
        started_at=profile.started_at,
        ended_at=profile.ended_at,
        metadata=_metadata(
            semantic_kind="conversation",
            timezone_name=timezone_name,
            chat_session=profile.title,
            message_count=profile.message_count,
            participant_count=len(profile.participant_ids),
        ),
        events=events,
        provider_updated_at=profile.ended_at,
        authority_at=profile.ended_at,
        authority_kind="provider_updated_at",
    )


def _asset_index(
    inventory: IMessageInventory,
) -> tuple[dict[str, IMessageSourceFile], dict[str, list[IMessageSourceFile]]]:
    exact: dict[str, IMessageSourceFile] = {}
    basenames: dict[str, list[IMessageSourceFile]] = defaultdict(list)
    for item in inventory.files:
        if item.accounting_category != "attachment":
            continue
        normalized = item.relative_path.replace("\\", "/").casefold()
        exact[normalized] = item
        basenames[PurePosixPath(normalized).name].append(item)
    return exact, dict(basenames)


def _resolve_asset(
    reference: str,
    exact: dict[str, IMessageSourceFile],
    basenames: dict[str, list[IMessageSourceFile]],
) -> IMessageSourceFile | None:
    normalized = reference.replace("\\", "/").lstrip("./").casefold()
    direct = exact.get(normalized)
    if direct is not None:
        return direct
    matches = basenames.get(PurePosixPath(normalized).name, ())
    return matches[0] if len(matches) == 1 else None


def _row_objects(
    row: dict[str, str],
    message_id: str,
    profile: FileProfile,
    timezone: ZoneInfo,
    timezone_name: str,
    asset_exact: dict[str, IMessageSourceFile],
    asset_basenames: dict[str, list[IMessageSourceFile]],
) -> tuple[
    list[NormalizedEvent],
    list[NormalizedRecord],
    list[NormalizedAsset],
    list[NormalizedRelationship],
]:
    occurred_at = _timestamp(row["Message Date"], timezone)
    if occurred_at is None:
        raise IMessageAdapterError("message row has no timestamp")
    authority_at = _authority(row, timezone)
    direction = row["Type"].strip().casefold() or "unknown"
    if direction not in {"incoming", "outgoing", "notification"}:
        raise IMessageAdapterError("message export contains an unsupported message type")
    participant_id = _participant_id(_participant_identity(row))
    reply_target = None
    reply_metadata: dict[str, object] | None = None
    if row["Replying to"].strip():
        reply_target, reply_metadata = _resolve_reply(row, profile)
    lifecycle_state = (
        "provider_deleted"
        if row["Deleted Date"].strip() or row["Status"].strip().casefold() == "recently deleted"
        else "active"
    )
    main = NormalizedEvent(
        occurred_at=occurred_at,
        source=SOURCE,
        event_type=f"message.{direction}",
        source_record_namespace=MESSAGE_NAMESPACE,
        actor_identity=_participant_identity(row),
        text=row["Text"] or None,
        metadata=_metadata(
            semantic_kind="message",
            timezone_name=timezone_name,
            service=row["Service"] or None,
            direction=row["Type"] or None,
            sender_id=row["Sender ID"] or None,
            sender_name=row["Sender Name"] or None,
            status=row["Status"] or None,
            subject=row["Subject"] or None,
            delivered_date=row["Delivered Date"] or None,
            read_date=row["Read Date"] or None,
            edited_date=row["Edited Date"] or None,
            deleted_date=row["Deleted Date"] or None,
            replying_to=row["Replying to"] or None,
            reactions=row["Reactions"] or None,
            attachment=row["Attachment"] or None,
            attachment_type=row["Attachment type"] or None,
        ),
        source_record_id=message_id,
        source_node_id=message_id,
        parent_source_node_id=reply_target,
        provider_updated_at=authority_at,
        authority_at=authority_at,
        authority_kind="provider_updated_at",
        lifecycle_state=lifecycle_state,
    )
    events = [main]
    records: list[NormalizedRecord] = []
    assets: list[NormalizedAsset] = []
    relationships = [
        _relationship(
            "message_from_participant",
            "event",
            MESSAGE_NAMESPACE,
            message_id,
            "record",
            PARTICIPANT_NAMESPACE,
            participant_id,
            authority_at=authority_at,
            metadata=_metadata(
                semantic_kind="message_sender",
                timezone_name=timezone_name,
            ),
        )
    ]

    if reply_metadata is not None:
        if reply_target is None:
            reference_id = (
                f"reply-reference:{_digest({'message': message_id, 'reply': row['Replying to']})}"
            )
            records.append(
                NormalizedRecord(
                    source=SOURCE,
                    record_type="message_reply_reference",
                    source_record_namespace="imazing_csv/reply_reference",
                    source_record_id=reference_id,
                    text=row["Replying to"],
                    occurred_at=occurred_at,
                    metadata=_metadata(
                        semantic_kind="reply_reference",
                        timezone_name=timezone_name,
                        **reply_metadata,
                    ),
                    provider_updated_at=authority_at,
                    authority_at=authority_at,
                    authority_kind="provider_updated_at",
                )
            )
            object_kind = "record"
            object_namespace = "imazing_csv/reply_reference"
            object_id = reference_id
        else:
            object_kind = "event"
            object_namespace = MESSAGE_NAMESPACE
            object_id = reply_target
        relationships.append(
            _relationship(
                "message_replies_to",
                "event",
                MESSAGE_NAMESPACE,
                message_id,
                object_kind,
                object_namespace,
                object_id,
                authority_at=authority_at,
                metadata=_metadata(
                    semantic_kind="reply",
                    timezone_name=timezone_name,
                    **reply_metadata,
                ),
            )
        )

    for ordinal, raw_reaction in enumerate(_reaction_parts(row["Reactions"]), start=1):
        actor, reaction_text, reaction_at, reaction_action = _reaction(raw_reaction, timezone)
        reaction_id = f"reaction:{_digest({'message': message_id, 'raw': raw_reaction, 'occurrence': ordinal})}"
        effective_at = reaction_at or authority_at
        reaction_event = NormalizedEvent(
            occurred_at=reaction_at,
            source=SOURCE,
            event_type=(
                "message.reaction_removed" if reaction_action == "removed" else "message.reaction"
            ),
            source_record_namespace="imazing_csv/reaction",
            actor_identity=actor,
            text=reaction_text,
            metadata=_metadata(
                semantic_kind="reaction",
                timezone_name=timezone_name,
                raw=raw_reaction,
                parsed=bool(reaction_at),
                action=reaction_action,
            ),
            source_record_id=reaction_id,
            source_node_id=reaction_id,
            parent_source_node_id=message_id,
            provider_updated_at=effective_at,
            authority_at=effective_at,
            authority_kind="provider_updated_at",
        )
        events.append(reaction_event)
        relationships.append(
            _relationship(
                "reaction_to_message",
                "event",
                "imazing_csv/reaction",
                reaction_id,
                "event",
                MESSAGE_NAMESPACE,
                message_id,
                authority_at=effective_at,
                metadata=_metadata(
                    semantic_kind="reaction_target",
                    timezone_name=timezone_name,
                ),
            )
        )

    for field_name, event_type, namespace, relationship_type in (
        ("Edited Date", "message.edit", "imazing_csv/edit", "edit_of_message"),
        ("Deleted Date", "message.deletion", "imazing_csv/deletion", "deletion_of_message"),
    ):
        event_at = _timestamp(row[field_name], timezone)
        if event_at is None:
            continue
        lifecycle_id = f"{event_type}:{_digest({'message': message_id, 'at': row[field_name]})}"
        events.append(
            NormalizedEvent(
                occurred_at=event_at,
                source=SOURCE,
                event_type=event_type,
                source_record_namespace=namespace,
                metadata=_metadata(
                    semantic_kind=event_type.removeprefix("message."),
                    timezone_name=timezone_name,
                ),
                source_record_id=lifecycle_id,
                source_node_id=lifecycle_id,
                parent_source_node_id=message_id,
                provider_updated_at=event_at,
                authority_at=event_at,
                authority_kind="provider_updated_at",
            )
        )
        relationships.append(
            _relationship(
                relationship_type,
                "event",
                namespace,
                lifecycle_id,
                "event",
                MESSAGE_NAMESPACE,
                message_id,
                authority_at=event_at,
                metadata=_metadata(
                    semantic_kind=relationship_type,
                    timezone_name=timezone_name,
                ),
            )
        )

    attachment = row["Attachment"].strip()
    if attachment:
        local = _resolve_asset(attachment, asset_exact, asset_basenames)
        filename = (
            local.filename
            if local is not None
            else PurePosixPath(attachment.replace("\\", "/")).name or "attachment"
        )
        asset_id = f"attachment:{_digest({'message': message_id, 'reference': attachment})}"
        mime_type = mimetypes.guess_type(filename)[0]
        assets.append(
            NormalizedAsset(
                source=SOURCE,
                asset_namespace=ATTACHMENT_NAMESPACE,
                source_asset_id=asset_id,
                sha256=local.sha256 if local is not None else None,
                mime_type=mime_type,
                filename=filename,
                original_path=attachment,
                size_bytes=local.size_bytes if local is not None else 0,
                captured_at=occurred_at,
                metadata=_metadata(
                    semantic_kind="message_attachment",
                    timezone_name=timezone_name,
                    attachment_type=row["Attachment type"] or None,
                    csv_reference=attachment,
                    available_locally=local is not None,
                    local_member_id=local.member_id if local is not None else None,
                ),
                provider_updated_at=authority_at,
                authority_at=authority_at,
                authority_kind="provider_updated_at",
            )
        )
        relationships.append(
            _relationship(
                "message_has_attachment",
                "event",
                MESSAGE_NAMESPACE,
                message_id,
                "asset",
                ATTACHMENT_NAMESPACE,
                asset_id,
                authority_at=authority_at,
                metadata=_metadata(
                    semantic_kind="message_attachment_link",
                    timezone_name=timezone_name,
                ),
            )
        )
    return events, records, assets, relationships


def iter_imessage_exports(
    member: IMessageSourceFile,
    inventory: IMessageInventory,
    *,
    timezone_name: str,
    device_label: str | None = None,
    batch_rows: int = CSV_BATCH_ROWS,
) -> Iterator[ParsedExport]:
    """Stream bounded normalized chunks from one independently acquired iMazing CSV."""

    if member.accounting_category != "classified_csv":
        raise IMessageAdapterError("only classified Messages CSV files can be parsed")
    if batch_rows <= 0:
        raise ValueError("batch_rows must be positive")
    timezone = _timezone(timezone_name)
    profile = _profile(member, timezone)
    if not profile.conversations:
        raise IMessageAdapterError("message CSV contains no records")
    asset_exact, asset_basenames = _asset_index(inventory)
    parser_version = f"{PARSER_VERSION}+tz={timezone_name}"
    export_label = (device_label or "unspecified-device").strip() or "unspecified-device"
    export_id = f"imazing:{export_label}:{member.sha256[:16]}"
    occurrences: Counter[tuple[str, ...]] = Counter()
    chunk_rows: list[tuple[dict[str, str], str]] = []
    chunk_index = 0

    def emit(rows: list[tuple[dict[str, str], str]], *, first: bool) -> ParsedExport:
        nonlocal chunk_index
        chunk_index += 1
        event_groups: dict[str, list[NormalizedEvent]] = defaultdict(list)
        records: list[NormalizedRecord] = []
        assets: list[NormalizedAsset] = []
        relationships: list[NormalizedRelationship] = []
        if first:
            records.extend(
                _participant_record(item, timezone_name)
                for item in sorted(profile.participants.values(), key=lambda item: item.source_id)
            )
            for conversation in profile.conversations.values():
                for participant_id in sorted(conversation.participant_ids):
                    relationships.append(
                        _relationship(
                            "conversation_has_participant",
                            "conversation",
                            CONVERSATION_NAMESPACE,
                            conversation.source_id,
                            "record",
                            PARTICIPANT_NAMESPACE,
                            participant_id,
                            authority_at=conversation.ended_at,
                            metadata=_metadata(
                                semantic_kind="conversation_participant",
                                timezone_name=timezone_name,
                            ),
                        )
                    )
        for row, message_id in rows:
            row_events, row_records, row_assets, row_relationships = _row_objects(
                row,
                message_id,
                profile,
                timezone,
                timezone_name,
                asset_exact,
                asset_basenames,
            )
            event_groups[_conversation_id(row["Chat Session"])].extend(row_events)
            records.extend(row_records)
            assets.extend(row_assets)
            relationships.extend(row_relationships)
        conversation_ids = set(event_groups)
        if first:
            conversation_ids.update(profile.conversations)
        conversations = tuple(
            _conversation(
                profile.conversations[conversation_id],
                timezone_name,
                tuple(event_groups.get(conversation_id, ())),
            )
            for conversation_id in sorted(conversation_ids)
        )
        return ParsedExport(
            source=SOURCE,
            parser_version=parser_version,
            category=f"imazing_csv/chunk/{chunk_index:08d}",
            filename=member.filename,
            original_path=member.source_path,
            sha256=member.sha256,
            size_bytes=member.size_bytes,
            export_id=export_id,
            snapshot_kind="partial",
            snapshot_scope="imessages_device_export",
            conversations=conversations,
            records=tuple(records),
            assets=tuple(assets),
            relationships=tuple(relationships),
        )

    for row in _rows(member):
        base = _message_base(row)
        occurrences[base] += 1
        chunk_rows.append((row, _message_id(row, occurrences[base])))
        if len(chunk_rows) >= batch_rows:
            yield emit(chunk_rows, first=chunk_index == 0)
            chunk_rows = []
    if chunk_rows:
        yield emit(chunk_rows, first=chunk_index == 0)
    if chunk_index == 0:
        raise IMessageAdapterError("message CSV produced no parsed chunks")
