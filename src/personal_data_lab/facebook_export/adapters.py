from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Protocol, runtime_checkable

from ..fingerprints import canonical_json_bytes
from ..models import (
    NormalizedAsset,
    NormalizedConversation,
    NormalizedEvent,
    NormalizedRecord,
    NormalizedRelationship,
    ParsedExport,
)
from .inventory import FacebookMember

PARSER_VERSION = "facebook-export-v1"
MAX_TEXT_BYTES = 64 * 1024 * 1024
CONVERSATION_NAMESPACE = "facebook/messages/conversation"


class FacebookAdapterError(ValueError):
    """Raised when a classified Facebook member cannot be represented safely."""


@runtime_checkable
class FacebookAdapter(Protocol):
    adapter_name: str
    parser_version: str
    support_level: str
    interpretation_level: str

    def parse(self, stream: BinaryIO, member: FacebookMember) -> ParsedExport: ...


def _json_safe(value: object) -> object:
    """Escape embedded NUL bytes PostgreSQL's text and jsonb types cannot store.

    Some Facebook fields (observed in IP-address entries) contain a literal U+0000
    codepoint. Postgres rejects it outright in both text and jsonb columns, so it is
    replaced with its visible escape form rather than silently dropped.
    """
    if isinstance(value, str):
        return value.replace("\x00", "\\u0000") if "\x00" in value else value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _fix_mojibake(text: str) -> str:
    """Undo Facebook's UTF-8-as-Latin-1 JSON export bug without guessing at intent.

    Facebook's export encodes non-ASCII text (names, emoji, curly quotes) by treating
    each UTF-8 byte as a Latin-1 codepoint before JSON-escaping it. Round-tripping
    only succeeds when the original bytes really were valid UTF-8; anything else,
    including genuinely ASCII-only text, is returned unchanged.
    """
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _string(value: object | None) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    result = str(value).strip()
    if not result:
        return None
    fixed = _fix_mojibake(result)
    return fixed.replace("\x00", "\\u0000") if "\x00" in fixed else fixed


def _timestamp(value: object) -> datetime | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number <= 0:
        return None
    if number > 10_000_000_000:
        number /= 1000
    try:
        return datetime.fromtimestamp(number, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None


def _first_value(value: dict[str, object], names: tuple[str, ...]) -> object | None:
    for name in names:
        item = value.get(name)
        if item not in (None, "", [], {}):
            return item
    return None


_TIME_NAMES = (
    "timestamp_ms",
    "timestamp",
    "creation_timestamp",
    "start_timestamp",
    "end_timestamp",
    "updated_timestamp",
)
_TITLE_NAMES = ("title", "name", "event_type")
_TEXT_NAMES = ("content", "text", "description", "share_text")


def _identity(value: object, occurrence: int = 1) -> str:
    base = f"content:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"
    return base if occurrence == 1 else f"{base}#occurrence={occurrence}"


def _dataset_key(member: FacebookMember) -> str:
    stem = Path(member.filename).stem.casefold()
    stem = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    return stem[:80] or "data"


def _path_token(member: FacebookMember) -> str:
    return hashlib.sha256(member.member_path.encode()).hexdigest()[:24]


def _namespace(member: FacebookMember, kind: str) -> str:
    return f"facebook/{member.product_key}/{_dataset_key(member)}/{kind}"


def _metadata(
    member: FacebookMember, value: object, adapter: str, **extra: object
) -> dict[str, object]:
    return {
        "facebook": value,
        "_pdl": {
            "acquisition_method": "facebook_export",
            "adapter": adapter,
            "product": member.product_key,
            "dataset": _dataset_key(member),
            **extra,
        },
    }


def _export(
    member: FacebookMember,
    sha256: str,
    *,
    adapter: str,
    conversations: tuple[NormalizedConversation, ...] = (),
    records: tuple[NormalizedRecord, ...] = (),
    events: tuple[NormalizedEvent, ...] = (),
    assets: tuple[NormalizedAsset, ...] = (),
    relationships: tuple[NormalizedRelationship, ...] = (),
    warnings: tuple[str, ...] = (),
) -> ParsedExport:
    category = f"facebook/{member.product_key}/{_dataset_key(member)}/{_path_token(member)}"
    return ParsedExport(
        source="facebook",
        parser_version=f"{PARSER_VERSION}/{adapter}",
        category=category,
        filename=member.filename,
        original_path=member.source_locator,
        sha256=sha256,
        size_bytes=member.uncompressed_size,
        snapshot_kind="partial",
        snapshot_scope=f"account/facebook_export/{member.product_key}",
        parse_warnings=warnings,
        conversations=conversations,
        records=records,
        events=events,
        assets=assets,
        relationships=relationships,
    )


def _conversation_id(thread_key: str) -> str:
    return f"path:{hashlib.sha256(thread_key.encode()).hexdigest()[:24]}"


def _top_level_values(document: object) -> list[object]:
    """Unwrap Facebook's common single-key JSON envelope without inventing a schema.

    Facebook wraps most category files as {"some_v2_key": [...]}. A dict with exactly
    one key whose value is a list is a pure envelope and is unwrapped; anything else
    (a bare list, or a dict with multiple/mixed fields such as a settings object) is
    kept as-is so no field is silently dropped or misattributed.
    """
    if isinstance(document, list):
        return document
    if isinstance(document, dict):
        list_keys = [key for key, value in document.items() if isinstance(value, list)]
        if len(document) == 1 and len(list_keys) == 1:
            return list(document[list_keys[0]])
        return [document]
    return [document]


def _objects(
    member: FacebookMember, values: list[object], *, adapter: str
) -> tuple[tuple[NormalizedRecord, ...], tuple[NormalizedEvent, ...]]:
    occurrences: defaultdict[str, int] = defaultdict(int)
    records: list[NormalizedRecord] = []
    events: list[NormalizedEvent] = []
    record_type = f"facebook_{member.product_key}_{_dataset_key(member)}"
    for raw in values:
        value = _json_safe(raw if isinstance(raw, dict) else {"value": raw})
        base = _identity(value)
        occurrences[base] += 1
        source_id = _identity(value, occurrences[base])
        occurred_at = _timestamp(_first_value(value, _TIME_NAMES))
        title = _string(_first_value(value, _TITLE_NAMES))
        text = _string(_first_value(value, _TEXT_NAMES))
        records.append(
            NormalizedRecord(
                source="facebook",
                record_type=record_type,
                source_record_namespace=_namespace(member, "record"),
                source_record_id=source_id,
                title=title,
                text=text,
                occurred_at=occurred_at,
                metadata=_metadata(member, value, adapter),
                authority_kind="unknown",
            )
        )
        if occurred_at is not None:
            events.append(
                NormalizedEvent(
                    occurred_at=occurred_at,
                    source="facebook",
                    event_type=record_type,
                    source_record_namespace=_namespace(member, "event"),
                    text=text or title,
                    source_record_id=source_id,
                    metadata=_metadata(member, value, adapter),
                    authority_kind="unknown",
                )
            )
    return tuple(records), tuple(events)


class StructuredAdapter:
    """Losslessly normalize Facebook JSON without inventing per-category schemas.

    This is a production representation contract, not a claim of product-specific
    interpretation: complete JSON values are retained in provider metadata, and only
    a generic single-key list envelope is unwrapped for record granularity.
    """

    support_level = "losslessly_structured"
    interpretation_level = "generic_lossless"
    adapter_name = "facebook_structured"
    parser_version = f"{PARSER_VERSION}/structured"

    def parse(self, stream: BinaryIO, member: FacebookMember) -> ParsedExport:
        content = stream.read(MAX_TEXT_BYTES + 1)
        if len(content) > MAX_TEXT_BYTES:
            raise FacebookAdapterError("bounded structured parser limit exceeded")
        sha256 = hashlib.sha256(content).hexdigest()
        try:
            document = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FacebookAdapterError("malformed Facebook JSON member") from error
        values = _top_level_values(document)
        records, events = _objects(member, values, adapter=self.adapter_name)
        return _export(member, sha256, adapter=self.adapter_name, records=records, events=events)


class MessagesAdapter:
    """Normalize one Messenger thread export file into a conversation of events."""

    support_level = "fully_structured"
    interpretation_level = "provider_specific"
    adapter_name = "facebook_messages"
    parser_version = f"{PARSER_VERSION}/messages"

    def parse(self, stream: BinaryIO, member: FacebookMember) -> ParsedExport:
        content = stream.read(MAX_TEXT_BYTES + 1)
        if len(content) > MAX_TEXT_BYTES:
            raise FacebookAdapterError("message thread exceeds bounded parser limit")
        sha256 = hashlib.sha256(content).hexdigest()
        try:
            document = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FacebookAdapterError("malformed Facebook message thread") from error
        if not isinstance(document, dict) or not isinstance(document.get("messages"), list):
            raise FacebookAdapterError("Facebook message thread missing a messages array")
        thread_key = member.thread_key
        if thread_key is None:
            raise FacebookAdapterError("message thread path does not resolve to a thread")
        conversation_id = _conversation_id(thread_key)
        participants_raw = document.get("participants") or []
        participants = [
            name
            for entry in participants_raw
            if isinstance(entry, dict) and (name := _string(entry.get("name")))
        ]
        title = _string(document.get("title")) or (", ".join(participants) or None)
        occurrences: defaultdict[str, int] = defaultdict(int)
        events: list[NormalizedEvent] = []
        for unsafe_message in document["messages"]:
            if not isinstance(unsafe_message, dict):
                continue
            raw_message = _json_safe(unsafe_message)
            # Scope identity to this thread: two different conversations can contain a
            # byte-identical message (e.g. a generic missed-call notice), and without the
            # thread in the hash they would collide onto the same canonical event.
            keyed_message = {"thread": thread_key, "message": raw_message}
            base = _identity(keyed_message)
            occurrences[base] += 1
            source_id = _identity(keyed_message, occurrences[base])
            occurred_at = _timestamp(raw_message.get("timestamp_ms"))
            event_type = "call" if "call_duration" in raw_message else "message"
            events.append(
                NormalizedEvent(
                    occurred_at=occurred_at,
                    source="facebook",
                    event_type=event_type,
                    source_record_namespace=f"{CONVERSATION_NAMESPACE}/event",
                    actor_identity=_string(raw_message.get("sender_name")),
                    source_record_id=source_id,
                    text=_string(raw_message.get("content")),
                    metadata=_metadata(member, raw_message, self.adapter_name, semantic_kind=event_type),
                    authority_kind="unknown",
                )
            )
        # A thread's own metadata (title/participants/thread_path) is duplicated identically
        # in every message_N.json part Facebook splits a long conversation across, so it is
        # safe to reassert per part. started_at/ended_at and the per-file dataset key are not
        # duplicated identically -- they would vary by which part happened to be parsed --
        # so they are deliberately left out of the conversation's own canonical fields
        # (unlike the file-scoped events below, one conversation row must stay identical no
        # matter which part last wrote it, or two genuine parts of one thread would conflict).
        conversation = NormalizedConversation(
            source="facebook",
            source_conversation_id=conversation_id,
            source_record_namespace=CONVERSATION_NAMESPACE,
            title=title,
            metadata={
                "facebook": _json_safe(
                    {
                        "participants": document.get("participants"),
                        "thread_path": document.get("thread_path"),
                        "is_still_participant": document.get("is_still_participant"),
                    }
                ),
                "_pdl": {
                    "acquisition_method": "facebook_export",
                    "adapter": self.adapter_name,
                    "product": member.product_key,
                },
            },
            events=tuple(events),
            authority_kind="unknown",
        )
        return _export(member, sha256, adapter=self.adapter_name, conversations=(conversation,))


class AssetAdapter:
    """Reference binary Facebook media (photos, videos, audio, documents) externally."""

    support_level = "fully_structured"
    interpretation_level = "provider_specific"
    adapter_name = "facebook_asset"
    parser_version = f"{PARSER_VERSION}/asset"

    def parse(self, stream: BinaryIO, member: FacebookMember) -> ParsedExport:
        digest = hashlib.sha256()
        size = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
        if size != member.uncompressed_size:
            raise FacebookAdapterError("asset byte count does not match ZIP central directory")
        sha256 = digest.hexdigest()
        source_id = f"sha256:{sha256}"
        # Facebook assigns each upload instance its own opaque numeric filename, so the
        # same bytes (a photo forwarded to several threads, a reused meme) surface under a
        # different name per occurrence. Canonical fields must stay content-derived, not
        # occurrence-derived, or two genuine observations of identical content would
        # register as a fingerprint conflict. The originally observed name/path is not
        # semantically meaningful Facebook user data (it is Facebook's own storage id), so
        # nothing evidentiary is lost by not carrying it into the canonical payload.
        extension = Path(member.filename).suffix.casefold()
        asset = NormalizedAsset(
            source="facebook",
            asset_namespace=f"facebook/{member.product_key}/asset",
            source_asset_id=source_id,
            sha256=sha256,
            mime_type=mimetypes.guess_type(member.filename)[0],
            filename=f"{sha256}{extension}",
            original_path=member.source_locator,
            size_bytes=size,
            metadata={
                "facebook": {"external": True},
                "_pdl": {
                    "acquisition_method": "facebook_export",
                    "adapter": self.adapter_name,
                    "product": member.product_key,
                },
            },
            authority_kind="unknown",
        )
        relationships: tuple[NormalizedRelationship, ...] = ()
        thread_key = member.thread_key
        if thread_key is not None:
            conversation_id = _conversation_id(thread_key)
            rel_digest = hashlib.sha256(
                f"{conversation_id}\x00{source_id}".encode()
            ).hexdigest()
            relationships = (
                NormalizedRelationship(
                    source="facebook",
                    relationship_type="conversation_has_attachment",
                    source_relationship_namespace=f"{CONVERSATION_NAMESPACE}/attachment_relationship",
                    source_relationship_id=rel_digest,
                    subject_kind="conversation",
                    subject_source="facebook",
                    subject_namespace=CONVERSATION_NAMESPACE,
                    subject_source_id=conversation_id,
                    object_kind="asset",
                    object_source="facebook",
                    object_namespace=asset.asset_namespace,
                    object_source_id=source_id,
                    metadata={"_pdl": {"provider_evidence": "shared_thread_directory"}},
                    authority_kind="unknown",
                ),
            )
        return _export(
            member,
            sha256,
            adapter=self.adapter_name,
            assets=(asset,),
            relationships=relationships,
        )


ADAPTERS: dict[str, FacebookAdapter] = {
    adapter.adapter_name: adapter
    for adapter in (StructuredAdapter(), MessagesAdapter(), AssetAdapter())
}
