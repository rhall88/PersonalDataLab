from __future__ import annotations

import hashlib
import html
import json
import mimetypes
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.message import Message
from email.parser import BytesFeedParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import BinaryIO

from ..models import (
    NormalizedAsset,
    NormalizedEvent,
    NormalizedRecord,
    NormalizedRelationship,
    ParsedExport,
)
from .adapters import GoogleAdapterError, MatchResult, open_takeout_member
from .inventory import TakeoutMember
from .products import SupportLevel
from .semantic import (
    SEMANTIC_MAX_MEMBER_BYTES,
    _authority,
    _component_properties,
    _decode_structured,
    _dedupe_identity,
    _first_property,
    _first_string,
    _identity,
    _parsed_export,
    _properties_dict,
    _read_bounded,
    _text_projection,
    _time,
    _unfold_lines,
)

MBOX_BATCH_MESSAGES = 250
MAX_MBOX_MESSAGE_BYTES = 128 * 1024 * 1024
MAX_NORMALIZED_MESSAGE_TEXT = 2 * 1024 * 1024


def _safe_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _strip_message_id(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().strip("<>").strip()
    return cleaned or None


def _thread_id(message: Message, message_id: str) -> str:
    provider_thread = message.get("X-GM-THRID")
    if provider_thread and provider_thread.strip():
        return f"provider:{provider_thread.strip()}"
    references = message.get("References")
    if references:
        first = next((item.strip("<>") for item in references.split() if item.strip("<>")), None)
        if first:
            return f"reference:{first}"
    reply_to = _strip_message_id(message.get("In-Reply-To"))
    if reply_to:
        return f"reference:{reply_to}"
    return f"message:{message_id}"


def _decoded_part(part: Message) -> bytes:
    decoded = part.get_payload(decode=True)
    if isinstance(decoded, bytes):
        return decoded
    payload = part.get_payload()
    if isinstance(payload, str):
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.encode(charset, errors="replace")
        except LookupError:
            return payload.encode("utf-8", errors="replace")
    return b""


def _body_and_attachments(
    message: Message,
    *,
    member: TakeoutMember,
    message_id: str,
) -> tuple[str | None, list[NormalizedAsset], list[str]]:
    text_parts: list[str] = []
    assets: list[NormalizedAsset] = []
    warnings: list[str] = []
    parts = message.walk() if message.is_multipart() else iter((message,))
    for index, part in enumerate(parts):
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        payload = _decoded_part(part)
        if filename or disposition == "attachment":
            safe_filename = filename or f"attachment-{index}"
            asset_id = hashlib.sha256(
                f"{message_id}\0{index}\0{safe_filename}".encode()
            ).hexdigest()
            assets.append(
                NormalizedAsset(
                    source="google",
                    asset_namespace="google_takeout/mail/attachment/mbox",
                    source_asset_id=asset_id,
                    sha256=hashlib.sha256(payload).hexdigest(),
                    mime_type=content_type,
                    filename=safe_filename,
                    original_path=f"{member.source_locator}#message/{message_id}/part/{index}",
                    size_bytes=len(payload),
                    metadata={
                        "google": {
                            "content_disposition": disposition,
                            "content_id": part.get("Content-ID"),
                        },
                        "_pdl": {"message_source_id": message_id, "part_index": index},
                    },
                    authority_kind="unknown",
                )
            )
            continue
        if content_type not in {"text/plain", "text/html"}:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            decoded_text = payload.decode(charset, errors="replace")
        except LookupError:
            decoded_text = payload.decode("utf-8", errors="replace")
        if content_type == "text/html":
            decoded_text = html.unescape(
                " ".join(
                    HTMLTextExtractor.text(decoded_text).split()
                )
            )
        text_parts.append(decoded_text)
    body = "\n".join(part for part in text_parts if part).strip() or None
    if body and len(body.encode("utf-8")) > MAX_NORMALIZED_MESSAGE_TEXT:
        body = body.encode("utf-8")[:MAX_NORMALIZED_MESSAGE_TEXT].decode(
            "utf-8", errors="ignore"
        )
        warnings.append("message text exceeded normalized projection limit")
    return body, assets, warnings


class HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.times: list[str] = []
        self.references: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in {"abbr", "time"}:
            candidate = attributes.get("title") or attributes.get("datetime")
            if candidate:
                self.times.append(candidate)
        if tag in {"a", "audio", "img", "source"}:
            candidate = attributes.get("href") or attributes.get("src")
            if candidate:
                self.references.append(candidate)

    @classmethod
    def parse(cls, source: str) -> HTMLTextExtractor:
        parser = cls()
        parser.feed(source)
        parser.close()
        return parser

    @classmethod
    def text(cls, source: str) -> str:
        return " ".join(cls.parse(source).parts)


def _message_objects(
    message: Message,
    *,
    raw_sha256: str,
    member: TakeoutMember,
) -> tuple[
    NormalizedRecord,
    NormalizedEvent,
    list[NormalizedAsset],
    list[NormalizedRelationship],
    list[str],
]:
    provider_message_id = _strip_message_id(message.get("Message-ID"))
    message_id = f"provider:{provider_message_id}" if provider_message_id else f"content:{raw_sha256}"
    thread_id = _thread_id(message, message_id)
    occurred_at = _safe_datetime(message.get("Date"))
    body, assets, warnings = _body_and_attachments(
        message,
        member=member,
        message_id=message_id,
    )
    headers = [{"name": name, "value": value} for name, value in message.raw_items()]
    thread = NormalizedRecord(
        source="google",
        record_type="conversation",
        source_record_namespace="google_takeout/mail/thread/mbox",
        source_record_id=thread_id,
        actor_identity="account_owner",
        metadata={
            "google": {"thread_id": message.get("X-GM-THRID")},
            "_pdl": {"semantic_adapter": "gmail_mbox"},
        },
        authority_kind="unknown",
    )
    event = NormalizedEvent(
        occurred_at=occurred_at,
        source="google",
        event_type="mail_message",
        source_record_namespace="google_takeout/mail/message/mbox",
        source_record_id=message_id,
        actor_identity=message.get("From"),
        text=body,
        metadata={
            "google": {"headers": headers},
            "_pdl": {
                "semantic_adapter": "gmail_mbox",
                "thread_source_id": thread_id,
                "raw_message_sha256": raw_sha256,
            },
        },
        authority_at=occurred_at,
        authority_kind="provider_updated_at" if occurred_at else "unknown",
    )
    relationships = [
        NormalizedRelationship(
            source="google",
            relationship_type="conversation_contains_message",
            source_relationship_namespace="google_takeout/mail/thread_message",
            source_relationship_id=hashlib.sha256(
                f"{thread_id}\0{message_id}".encode()
            ).hexdigest(),
            subject_kind="record",
            subject_source="google",
            subject_namespace="google_takeout/mail/thread/mbox",
            subject_source_id=thread_id,
            object_kind="event",
            object_source="google",
            object_namespace="google_takeout/mail/message/mbox",
            object_source_id=message_id,
            metadata={"_pdl": {"provider_evidenced": True}},
            authority_at=occurred_at,
            authority_kind="provider_updated_at" if occurred_at else "unknown",
        )
    ]
    for asset in assets:
        relationships.append(
            NormalizedRelationship(
                source="google",
                relationship_type="message_has_attachment",
                source_relationship_namespace="google_takeout/mail/message_attachment",
                source_relationship_id=hashlib.sha256(
                    f"{message_id}\0{asset.source_asset_id}".encode()
                ).hexdigest(),
                subject_kind="event",
                subject_source="google",
                subject_namespace="google_takeout/mail/message/mbox",
                subject_source_id=message_id,
                object_kind="asset",
                object_source="google",
                object_namespace=asset.asset_namespace,
                object_source_id=asset.source_asset_id,
                metadata={"_pdl": {"provider_evidenced": True}},
                authority_at=occurred_at,
                authority_kind="provider_updated_at" if occurred_at else "unknown",
            )
        )
    return thread, event, assets, relationships, warnings


def _iter_mbox_messages(stream: BinaryIO) -> Iterator[tuple[Message | None, str, bool]]:
    parser: BytesFeedParser | None = None
    digest = hashlib.sha256()
    size = 0
    oversized = False

    def finish() -> tuple[Message | None, str, bool] | None:
        nonlocal parser, digest, size, oversized
        if parser is None:
            return None
        message = None if oversized else parser.close()
        result = (message, digest.hexdigest(), oversized)
        parser = None
        digest = hashlib.sha256()
        size = 0
        oversized = False
        return result

    for line in stream:
        if line.startswith(b"From "):
            if (completed := finish()) is not None:
                yield completed
            parser = BytesFeedParser(policy=policy.default)
            continue
        if parser is None:
            if not line.strip():
                continue
            parser = BytesFeedParser(policy=policy.default)
        digest.update(line)
        size += len(line)
        if size > MAX_MBOX_MESSAGE_BYTES:
            oversized = True
        elif not oversized:
            parser.feed(line)
    if (completed := finish()) is not None:
        yield completed


class MboxAdapter:
    adapter_name = "gmail_mbox"
    parser_version = "google-gmail-mbox-v1"
    product_keys = ("mail", "groups")
    supported_patterns = ("**/*.mbox",)
    support_level: SupportLevel = "fully_structured"
    representation_kind = "records+events+assets+relationships"
    is_fallback = False

    def probe(self, member: TakeoutMember) -> MatchResult:
        return MatchResult(
            member.product_key in self.product_keys
            and member.extension == ".mbox"
            and not member.is_directory,
            "bounded streaming MBOX representation",
            requires_byte_level_inspection=True,
        )

    def parse(self, stream: BinaryIO, member: TakeoutMember) -> ParsedExport:
        raise GoogleAdapterError("MBOX requires the streaming adapter path")

    def iter_parsed_exports(self, member: TakeoutMember) -> Iterator[ParsedExport]:
        source_digest = hashlib.sha256()
        with open_takeout_member(member) as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                source_digest.update(chunk)
        source_sha256 = source_digest.hexdigest()
        product_key = member.product_key or "mail"
        batch_records: dict[str, NormalizedRecord] = {}
        batch_events: list[NormalizedEvent] = []
        batch_assets: list[NormalizedAsset] = []
        batch_relationships: list[NormalizedRelationship] = []
        batch_warnings: list[str] = []
        chunk_index = 0

        def emit() -> ParsedExport:
            return ParsedExport(
                source="google",
                parser_version=self.parser_version,
                category=f"takeout/{product_key}/semantic/mbox/chunk/{chunk_index:08d}",
                filename=member.member_path,
                original_path=member.source_locator,
                sha256=source_sha256,
                size_bytes=member.uncompressed_size,
                exported_at=member.exported_at,
                export_id=member.export_id,
                snapshot_kind="partial",
                snapshot_scope=f"account/takeout/{product_key}",
                parse_warnings=tuple(batch_warnings),
                records=tuple(batch_records.values()),
                events=tuple(batch_events),
                assets=tuple(batch_assets),
                relationships=tuple(batch_relationships),
            )

        with open_takeout_member(member) as source:
            for message, raw_sha256, oversized in _iter_mbox_messages(source):
                if oversized or message is None:
                    message_id = f"content:{raw_sha256}"
                    authority_at, authority_kind = _authority(member, None)
                    batch_events.append(
                        NormalizedEvent(
                            occurred_at=None,
                            source="google",
                            event_type="mail_message_oversized",
                            source_record_namespace="google_takeout/mail/message/mbox",
                            source_record_id=message_id,
                            metadata={
                                "_pdl": {
                                    "semantic_adapter": self.adapter_name,
                                    "raw_message_sha256": raw_sha256,
                                    "bounded_limit_bytes": MAX_MBOX_MESSAGE_BYTES,
                                }
                            },
                            authority_at=authority_at,
                            authority_kind=authority_kind,
                        )
                    )
                    batch_warnings.append("MBOX message exceeded bounded parser limit")
                else:
                    thread, event, assets, relationships, warnings = _message_objects(
                        message,
                        raw_sha256=raw_sha256,
                        member=member,
                    )
                    batch_records.setdefault(thread.source_record_id, thread)
                    batch_events.append(event)
                    batch_assets.extend(assets)
                    batch_relationships.extend(relationships)
                    batch_warnings.extend(warnings)
                if len(batch_events) >= MBOX_BATCH_MESSAGES:
                    chunk_index += 1
                    yield emit()
                    batch_records.clear()
                    batch_events.clear()
                    batch_assets.clear()
                    batch_relationships.clear()
                    batch_warnings.clear()
        if batch_events or chunk_index == 0:
            chunk_index += 1
            yield emit()


@dataclass(frozen=True)
class ExternalAssetAdapter:
    adapter_name: str
    parser_version: str
    product_keys: tuple[str, ...]
    excluded_extensions: tuple[str, ...]
    supported_patterns: tuple[str, ...] = ("**/*",)
    support_level: SupportLevel = "fully_structured"
    representation_kind: str = "assets"
    is_fallback: bool = False

    def probe(self, member: TakeoutMember) -> MatchResult:
        return MatchResult(
            member.product_key in self.product_keys
            and member.extension not in self.excluded_extensions
            and not member.is_directory
            and member.accounting_category == "classified",
            "external asset representation",
            requires_byte_level_inspection=True,
        )

    def parse(self, stream: BinaryIO, member: TakeoutMember) -> ParsedExport:
        digest = hashlib.sha256()
        size = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
        if size != member.uncompressed_size:
            raise GoogleAdapterError("asset size changed while reading")
        content_sha256 = digest.hexdigest()
        path_identity = member.product_relative_path or member.member_path
        source_asset_id = "path:" + hashlib.sha256(path_identity.encode()).hexdigest()
        authority_at, authority_kind = _authority(member, None)
        asset = NormalizedAsset(
            source="google",
            asset_namespace=f"google_takeout/{member.product_key}/asset",
            source_asset_id=source_asset_id,
            sha256=content_sha256,
            mime_type=mimetypes.guess_type(member.filename)[0],
            filename=member.filename,
            original_path=member.source_locator,
            size_bytes=size,
            metadata={
                "_pdl": {
                    "semantic_adapter": self.adapter_name,
                    "takeout_product": member.product_key,
                    "external_content": True,
                }
            },
            authority_at=authority_at,
            authority_kind=authority_kind,
        )
        return _parsed_export(
            member=member,
            content=b"",
            parser_version=self.parser_version,
            category=f"takeout/{member.product_key}/semantic/asset",
            assets=(asset,),
        ).model_copy(update={"sha256": content_sha256, "size_bytes": size})


class CommunicationsStructuredAdapter:
    adapter_name = "communications_semantic"
    parser_version = "google-communications-v1"
    product_keys = ("mail", "google_chat", "voice", "google_meet", "groups")
    supported_patterns = ("**/*.json", "**/*.csv", "**/*.html", "**/*.vcf")
    support_level: SupportLevel = "fully_structured"
    representation_kind = "records+events+assets+relationships"
    is_fallback = False

    def probe(self, member: TakeoutMember) -> MatchResult:
        return MatchResult(
            member.product_key in self.product_keys
            and member.extension in {".json", ".csv", ".html", ".htm", ".vcf"}
            and member.uncompressed_size <= SEMANTIC_MAX_MEMBER_BYTES
            and not member.is_directory,
            "communications structured representation",
            requires_byte_level_inspection=True,
        )

    def _voice_html(self, content: bytes, member: TakeoutMember) -> ParsedExport:
        text = content.decode("utf-8-sig", errors="strict")
        document = HTMLTextExtractor.parse(text)
        value = {"raw_html": text, "references": document.references}
        source_id = _identity(value, prefix="voice")
        occurred_at = next(
            (_time({"time": item}, ("time",)) for item in document.times if item),
            None,
        )
        authority_at, authority_kind = _authority(member, occurred_at)
        event = NormalizedEvent(
            occurred_at=occurred_at,
            source="google",
            event_type="voice_communication",
            source_record_namespace="google_takeout/voice/communication/html",
            source_record_id=source_id,
            actor_identity="account_owner",
            text=" ".join(document.parts) or None,
            metadata={
                "google": value,
                "_pdl": {"semantic_adapter": self.adapter_name},
            },
            authority_at=authority_at,
            authority_kind=authority_kind,
        )
        return _parsed_export(
            member=member,
            content=content,
            parser_version=self.parser_version,
            category="takeout/voice/semantic/html",
            events=(event,),
        )

    def _voice_vcard(self, content: bytes, member: TakeoutMember) -> ParsedExport:
        text = content.decode("utf-8-sig", errors="strict")
        records: list[NormalizedRecord] = []
        authority_at, authority_kind = _authority(member, None)
        for index, properties in enumerate(_component_properties(_unfold_lines(text), "VCARD")):
            raw = _properties_dict(properties)
            fn = _first_property(properties, "FN")
            records.append(
                NormalizedRecord(
                    source="google",
                    record_type="voice_contact",
                    source_record_namespace="google_takeout/voice/contact/vcard",
                    source_record_id=_identity(raw, prefix=str(index)),
                    title=fn[1] if fn else None,
                    metadata={
                        "google": raw,
                        "_pdl": {"semantic_adapter": self.adapter_name},
                    },
                    authority_at=authority_at,
                    authority_kind=authority_kind,
                )
            )
        return _parsed_export(
            member=member,
            content=content,
            parser_version=self.parser_version,
            category="takeout/voice/semantic/vcard",
            records=records,
        )

    def _chat_json(self, content: bytes, member: TakeoutMember) -> ParsedExport:
        decoded = json.loads(content.decode("utf-8-sig"))
        root = decoded if isinstance(decoded, dict) else {"messages": decoded}
        messages = root.get("messages")
        authority_at, authority_kind = _authority(member, None)
        if not isinstance(messages, list):
            record = NormalizedRecord(
                source="google",
                record_type="conversation",
                source_record_namespace="google_takeout/google_chat/space/json",
                source_record_id=_identity(root, prefix="space"),
                title=_first_string(root, ("name", "title")),
                metadata={
                    "google": root,
                    "_pdl": {"semantic_adapter": self.adapter_name},
                },
                authority_at=authority_at,
                authority_kind=authority_kind,
            )
            return _parsed_export(
                member=member,
                content=content,
                parser_version=self.parser_version,
                category="takeout/google_chat/semantic/json",
                records=(record,),
            )
        records: dict[str, NormalizedRecord] = {}
        events: list[NormalizedEvent] = []
        assets: list[NormalizedAsset] = []
        relationships: list[NormalizedRelationship] = []
        seen: dict[str, int] = {}
        for index, raw in enumerate(messages):
            value = raw if isinstance(raw, dict) else {"value": raw}
            message_id = _dedupe_identity(
                (
                    f"provider:{value['message_id']}"
                    if isinstance(value.get("message_id"), str) and value["message_id"]
                    else _identity(value, prefix=str(index))
                ),
                seen,
            )
            topic = value.get("topic_id")
            thread_id = (
                f"provider:{topic}"
                if isinstance(topic, str) and topic
                else f"member:{hashlib.sha256(member.member_path.encode()).hexdigest()}"
            )
            records.setdefault(
                thread_id,
                NormalizedRecord(
                    source="google",
                    record_type="conversation",
                    source_record_namespace="google_takeout/google_chat/conversation/json",
                    source_record_id=thread_id,
                    metadata={"_pdl": {"semantic_adapter": self.adapter_name}},
                    authority_at=authority_at,
                    authority_kind=authority_kind,
                ),
            )
            occurred_at = _time(value, ("created_date", "created", "time"))
            creator = value.get("creator") if isinstance(value.get("creator"), dict) else {}
            event_authority_at, event_authority_kind = _authority(member, occurred_at)
            events.append(
                NormalizedEvent(
                    occurred_at=occurred_at,
                    source="google",
                    event_type="chat_message",
                    source_record_namespace="google_takeout/google_chat/message/json",
                    source_record_id=message_id,
                    actor_identity=_first_string(creator, ("email", "name")),
                    text=_first_string(value, ("text", "message")),
                    metadata={
                        "google": value,
                        "_pdl": {"semantic_adapter": self.adapter_name, "source_order": index},
                    },
                    authority_at=event_authority_at,
                    authority_kind=event_authority_kind,
                )
            )
            relationships.append(
                NormalizedRelationship(
                    source="google",
                    relationship_type="conversation_contains_message",
                    source_relationship_namespace="google_takeout/google_chat/thread_message",
                    source_relationship_id=hashlib.sha256(
                        f"{thread_id}\0{message_id}".encode()
                    ).hexdigest(),
                    subject_kind="record",
                    subject_source="google",
                    subject_namespace="google_takeout/google_chat/conversation/json",
                    subject_source_id=thread_id,
                    object_kind="event",
                    object_source="google",
                    object_namespace="google_takeout/google_chat/message/json",
                    object_source_id=message_id,
                    metadata={"_pdl": {"provider_evidenced": True}},
                    authority_at=event_authority_at,
                    authority_kind=event_authority_kind,
                )
            )
            attached = value.get("attached_files")
            if isinstance(attached, list):
                for attachment_index, attachment in enumerate(attached):
                    attachment_value = (
                        attachment if isinstance(attachment, dict) else {"value": attachment}
                    )
                    filename = _first_string(
                        attachment_value, ("name", "filename", "export_name")
                    ) or f"attachment-{attachment_index}"
                    asset_id = hashlib.sha256(
                        f"{message_id}\0{attachment_index}\0{filename}".encode()
                    ).hexdigest()
                    asset = NormalizedAsset(
                        source="google",
                        asset_namespace="google_takeout/google_chat/attachment/json",
                        source_asset_id=asset_id,
                        mime_type=_first_string(attachment_value, ("mime_type", "content_type")),
                        filename=filename,
                        original_path=f"{member.source_locator}#message/{message_id}/{filename}",
                        size_bytes=0,
                        metadata={
                            "google": attachment_value,
                            "_pdl": {"message_source_id": message_id},
                        },
                        authority_at=event_authority_at,
                        authority_kind=event_authority_kind,
                    )
                    assets.append(asset)
                    relationships.append(
                        NormalizedRelationship(
                            source="google",
                            relationship_type="message_has_attachment",
                            source_relationship_namespace=(
                                "google_takeout/google_chat/message_attachment"
                            ),
                            source_relationship_id=hashlib.sha256(
                                f"{message_id}\0{asset_id}".encode()
                            ).hexdigest(),
                            subject_kind="event",
                            subject_source="google",
                            subject_namespace="google_takeout/google_chat/message/json",
                            subject_source_id=message_id,
                            object_kind="asset",
                            object_source="google",
                            object_namespace=asset.asset_namespace,
                            object_source_id=asset.source_asset_id,
                            metadata={"_pdl": {"provider_evidenced": True}},
                            authority_at=event_authority_at,
                            authority_kind=event_authority_kind,
                        )
                    )
        return _parsed_export(
            member=member,
            content=content,
            parser_version=self.parser_version,
            category="takeout/google_chat/semantic/json",
            records=records.values(),
            events=events,
            assets=assets,
            relationships=relationships,
        )

    def parse(self, stream: BinaryIO, member: TakeoutMember) -> ParsedExport:
        content = _read_bounded(stream, member)
        product_key = member.product_key
        if product_key == "voice" and member.extension in {".html", ".htm"}:
            return self._voice_html(content, member)
        if product_key == "voice" and member.extension == ".vcf":
            return self._voice_vcard(content, member)
        if product_key == "google_chat" and member.extension == ".json":
            return self._chat_json(content, member)
        values = _decode_structured(content, member.extension)
        records: list[NormalizedRecord] = []
        events: list[NormalizedEvent] = []
        seen: dict[str, int] = {}
        representation = member.extension.removeprefix(".")
        for index, raw in enumerate(values):
            value = raw if isinstance(raw, dict) else {"value": raw}
            source_id = _dedupe_identity(_identity(value, prefix=str(index)), seen)
            occurred_at = _time(value, ("Start Time", "created_date", "time", "date"))
            authority_at, authority_kind = _authority(member, occurred_at)
            if product_key == "google_meet":
                events.append(
                    NormalizedEvent(
                        occurred_at=occurred_at,
                        source="google",
                        event_type="meet_call",
                        source_record_namespace="google_takeout/google_meet/call/csv",
                        source_record_id=source_id,
                        actor_identity="account_owner",
                        text=None,
                        metadata={
                            "google": value,
                            "_pdl": {"semantic_adapter": self.adapter_name},
                        },
                        authority_at=authority_at,
                        authority_kind=authority_kind,
                    )
                )
            else:
                record_type = (
                    "mail_setting"
                    if product_key == "mail"
                    else "group_membership"
                    if product_key == "groups"
                    else "conversation"
                )
                records.append(
                    NormalizedRecord(
                        source="google",
                        record_type=record_type,
                        source_record_namespace=(
                            f"google_takeout/{product_key}/{record_type}/{representation}"
                        ),
                        source_record_id=source_id,
                        title=_first_string(value, ("Group Email", "name", "title")),
                        text=_text_projection(value),
                        occurred_at=occurred_at,
                        actor_identity="account_owner",
                        metadata={
                            "google": value,
                            "_pdl": {"semantic_adapter": self.adapter_name},
                        },
                        authority_at=authority_at,
                        authority_kind=authority_kind,
                    )
                )
        return _parsed_export(
            member=member,
            content=content,
            parser_version=self.parser_version,
            category=f"takeout/{product_key}/semantic/{representation}",
            records=records,
            events=events,
        )


COMMUNICATION_ASSET_EXTENSIONS = (
    ".json",
    ".csv",
    ".html",
    ".htm",
    ".vcf",
    ".mbox",
)

PR7_ADAPTERS = (
    MboxAdapter(),
    CommunicationsStructuredAdapter(),
    ExternalAssetAdapter(
        adapter_name="communications_asset",
        parser_version="google-communications-asset-v1",
        product_keys=("mail", "google_chat", "voice", "google_meet", "groups"),
        excluded_extensions=COMMUNICATION_ASSET_EXTENSIONS,
    ),
)
