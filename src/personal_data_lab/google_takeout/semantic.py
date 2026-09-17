from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import BinaryIO
from urllib.parse import urlparse
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..fingerprints import canonical_json_bytes
from ..google_importer import _timestamp
from ..models import (
    NormalizedAsset,
    NormalizedEvent,
    NormalizedRecord,
    NormalizedRelationship,
    ParsedExport,
)
from .adapters import GoogleAdapterError, MatchResult
from .inventory import MAX_GENERIC_MEMBER_BYTES, TakeoutMember
from .products import PRODUCT_REGISTRY, SupportLevel

SEMANTIC_MAX_MEMBER_BYTES = MAX_GENERIC_MEMBER_BYTES


def _read_bounded(stream: BinaryIO, member: TakeoutMember) -> bytes:
    if member.uncompressed_size > SEMANTIC_MAX_MEMBER_BYTES:
        raise GoogleAdapterError("semantic member exceeds the bounded parser limit")
    content = stream.read(SEMANTIC_MAX_MEMBER_BYTES + 1)
    if len(content) > SEMANTIC_MAX_MEMBER_BYTES:
        raise GoogleAdapterError("semantic member exceeds the bounded parser limit")
    if len(content) != member.uncompressed_size:
        raise GoogleAdapterError("member size changed while reading")
    return content


def _json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value} is not supported")


def _xml_value(element: ElementTree.Element) -> dict[str, object]:
    return {
        "tag": str(element.tag),
        "attributes": dict(element.attrib),
        "text": element.text,
        "tail": element.tail,
        "children": [_xml_value(child) for child in element],
    }


def _decode_structured(content: bytes, extension: str) -> list[object]:
    text = content.decode("utf-8-sig", errors="strict")
    if not text.strip():
        return []
    if extension == ".json":
        decoded = json.loads(text, parse_constant=_json_constant)
        return list(decoded) if isinstance(decoded, list) else [decoded]
    if extension == ".csv":
        reader = csv.DictReader(io.StringIO(text, newline=""))
        return [dict(row) for row in reader]
    if extension in {".xml", ".kml"}:
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise ValueError("XML declarations with entities are not supported")
        return [{"xml": _xml_value(ElementTree.fromstring(text)), "raw_xml": text}]
    if extension in {".html", ".htm"}:
        visible = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
        visible = re.sub(r"(?s)<[^>]+>", " ", visible)
        return [{"raw_html": text, "visible_text": " ".join(visible.split())}]
    if extension == ".txt":
        return [{"raw_text": text}]
    raise ValueError("unsupported structured representation")


def _provider_identifier(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    normalized = {str(key).casefold().replace("_", ""): item for key, item in value.items()}
    for key in (
        "id",
        "uid",
        "uuid",
        "resourceid",
        "recordid",
        "eventid",
        "taskid",
        "placeid",
        "messageid",
    ):
        candidate = normalized.get(key)
        if isinstance(candidate, (str, int)) and not isinstance(candidate, bool):
            cleaned = str(candidate).strip()
            if cleaned:
                return cleaned
    properties = value.get("properties")
    if isinstance(properties, dict):
        return _provider_identifier(properties)
    return None


def _first_string(value: object, names: Iterable[str]) -> str | None:
    if not isinstance(value, dict):
        return None
    folded = {str(key).casefold().replace("_", ""): item for key, item in value.items()}
    for name in names:
        candidate = folded.get(name.casefold().replace("_", ""))
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    properties = value.get("properties")
    if isinstance(properties, dict):
        return _first_string(properties, names)
    return None


def _text_projection(value: object) -> str | None:
    texts: list[str] = []

    def visit(item: object) -> None:
        if isinstance(item, str):
            cleaned = " ".join(item.split())
            if cleaned and cleaned not in texts:
                texts.append(cleaned)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            for key in sorted(item):
                visit(item[key])

    visit(value)
    return "\n".join(texts) or None


def _identity(value: object, *, prefix: str) -> str:
    provider_id = _provider_identifier(value)
    if provider_id is not None:
        return f"provider:{provider_id}"
    return f"content:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}:{prefix}"


def _time(value: object, names: Iterable[str]) -> datetime | None:
    return _timestamp(_first_string(value, names))


def _authority(member: TakeoutMember, provider_updated_at: datetime | None) -> tuple[datetime | None, str]:
    if provider_updated_at is not None:
        return provider_updated_at, "provider_updated_at"
    if member.exported_at is not None:
        return member.exported_at, "exported_at"
    return None, "unknown"


def _dedupe_identity(identity: str, seen: dict[str, int]) -> str:
    seen[identity] = seen.get(identity, 0) + 1
    return identity if seen[identity] == 1 else f"{identity}:{seen[identity]}"


def _parsed_export(
    *,
    member: TakeoutMember,
    content: bytes,
    parser_version: str,
    category: str,
    records: Iterable[NormalizedRecord] = (),
    events: Iterable[NormalizedEvent] = (),
    assets: Iterable[NormalizedAsset] = (),
    relationships: Iterable[NormalizedRelationship] = (),
    warnings: Iterable[str] = (),
) -> ParsedExport:
    product_key = member.product_key
    if product_key is None:
        raise GoogleAdapterError("semantic adapter requires a known Google product")
    return ParsedExport(
        source="google",
        parser_version=parser_version,
        category=category,
        filename=member.member_path,
        original_path=member.source_locator,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        exported_at=member.exported_at,
        export_id=member.export_id,
        snapshot_kind="partial",
        snapshot_scope=f"account/takeout/{product_key}",
        parse_warnings=tuple(warnings),
        records=tuple(records),
        events=tuple(events),
        assets=tuple(assets),
        relationships=tuple(relationships),
    )


_KIND_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("conversation", ("message", "conversation", "thread", "chat")),
    ("purchase", ("purchase", "order", "transaction")),
    ("reservation", ("reservation", "booking")),
    ("subscription", ("subscription", "membership")),
    ("document", ("document", "file")),
    ("note", ("note", "memo")),
    ("event", ("event", "activity", "notification", "alert")),
    ("saved_item", ("saved", "bookmark", "favorite")),
    ("device", ("device", "phone", "pixel")),
    ("account", ("account", "profile")),
)


def _choose_kind(product_key: str, value: object, member_path: str) -> str:
    available = PRODUCT_REGISTRY[product_key].record_kinds
    searchable = " ".join(
        [
            member_path.casefold(),
            *(str(key).casefold() for key in value if isinstance(value, dict)),
            _first_string(value, ("type", "kind", "category")) or "",
        ]
    )
    for kind, hints in _KIND_HINTS:
        if kind in available and any(hint in searchable for hint in hints):
            return kind
    non_asset = tuple(kind for kind in available if kind not in {"asset", "event"})
    if non_asset:
        return non_asset[0]
    return available[0] if available else f"{product_key}_record"


@dataclass(frozen=True)
class StructuredProductAdapter:
    adapter_name: str
    parser_version: str
    product_keys: tuple[str, ...]
    family: str
    supported_extensions: tuple[str, ...] = (".json", ".csv", ".xml", ".html", ".htm")
    supported_patterns: tuple[str, ...] = (
        "**/*.json",
        "**/*.csv",
        "**/*.xml",
        "**/*.html",
    )
    support_level: SupportLevel = "fully_structured"
    representation_kind: str = "records"
    is_fallback: bool = False

    def probe(self, member: TakeoutMember) -> MatchResult:
        matched = (
            member.product_key in self.product_keys
            and member.extension in self.supported_extensions
            and member.uncompressed_size <= SEMANTIC_MAX_MEMBER_BYTES
            and not member.is_directory
            and member.accounting_category == "classified"
        )
        return MatchResult(
            matched,
            f"{self.family} structured semantic representation",
            requires_byte_level_inspection=True,
        )

    def parse(self, stream: BinaryIO, member: TakeoutMember) -> ParsedExport:
        content = _read_bounded(stream, member)
        try:
            values = _decode_structured(content, member.extension)
        except (csv.Error, ElementTree.ParseError, UnicodeDecodeError, ValueError) as error:
            raise GoogleAdapterError("could not parse structured semantic member") from error
        product_key = member.product_key
        if product_key is None:
            raise GoogleAdapterError("semantic adapter requires a known Google product")
        records: list[NormalizedRecord] = []
        events: list[NormalizedEvent] = []
        seen: dict[str, int] = {}
        representation = member.extension.removeprefix(".")
        for index, value in enumerate(values):
            kind = _choose_kind(product_key, value, member.member_path)
            source_id = _dedupe_identity(
                _identity(value, prefix=str(index)),
                seen,
            )
            updated_at = _time(value, ("updated", "updated_at", "modified", "lastModified"))
            created_at = _time(value, ("created", "created_at", "creationTime"))
            occurred_at = _time(
                value,
                ("occurred", "occurred_at", "time", "date", "timestamp", "startTime"),
            )
            authority_at, authority_kind = _authority(member, updated_at)
            metadata = {
                "google": value,
                "_pdl": {
                    "takeout_product": product_key,
                    "representation": representation,
                    "semantic_adapter": self.adapter_name,
                    "semantic_family": self.family,
                    "source_order": index,
                },
            }
            namespace = f"google_takeout/{product_key}/{kind}/{representation}"
            if kind == "event":
                events.append(
                    NormalizedEvent(
                        occurred_at=occurred_at,
                        source="google",
                        event_type=f"{product_key}_event",
                        source_record_namespace=namespace,
                        source_record_id=source_id,
                        actor_identity="account_owner",
                        text=_text_projection(value),
                        metadata=metadata,
                        provider_updated_at=updated_at,
                        authority_at=authority_at,
                        authority_kind=authority_kind,
                    )
                )
            else:
                records.append(
                    NormalizedRecord(
                        source="google",
                        record_type=kind,
                        source_record_namespace=namespace,
                        source_record_id=source_id,
                        title=_first_string(value, ("title", "name", "subject", "label")),
                        text=_text_projection(value),
                        created_at=created_at,
                        updated_at=updated_at,
                        occurred_at=occurred_at,
                        actor_identity="account_owner",
                        metadata=metadata,
                        provider_updated_at=updated_at,
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


def _unfold_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _property(line: str) -> tuple[str, dict[str, str], str] | None:
    if ":" not in line:
        return None
    head, value = line.split(":", 1)
    pieces = head.split(";")
    name = pieces[0].upper()
    params = {
        key.upper(): item
        for piece in pieces[1:]
        if "=" in piece
        for key, item in (piece.split("=", 1),)
    }
    return name, params, value


def _component_properties(lines: list[str], component: str) -> list[list[tuple[str, dict[str, str], str]]]:
    result: list[list[tuple[str, dict[str, str], str]]] = []
    current: list[tuple[str, dict[str, str], str]] | None = None
    for line in lines:
        upper = line.upper()
        if upper == f"BEGIN:{component}":
            current = []
        elif upper == f"END:{component}" and current is not None:
            result.append(current)
            current = None
        elif current is not None and (parsed := _property(line)) is not None:
            current.append(parsed)
    return result


def _properties_dict(
    properties: list[tuple[str, dict[str, str], str]],
) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {}
    for name, params, value in properties:
        result.setdefault(name, []).append({"params": params, "value": value})
    return result


def _first_property(
    properties: list[tuple[str, dict[str, str], str]], name: str
) -> tuple[dict[str, str], str] | None:
    return next(((params, value) for key, params, value in properties if key == name), None)


def _calendar_datetime(item: tuple[dict[str, str], str] | None) -> datetime | None:
    if item is None:
        return None
    params, value = item
    try:
        if re.fullmatch(r"\d{8}", value):
            return datetime.strptime(value, "%Y%m%d").replace(tzinfo=UTC)
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
        timezone = params.get("TZID")
        if timezone:
            return parsed.replace(tzinfo=ZoneInfo(timezone)).astimezone(UTC)
        return parsed.replace(tzinfo=UTC)
    except (ValueError, ZoneInfoNotFoundError):
        return None


class CalendarAdapter:
    adapter_name = "calendar_semantic"
    parser_version = "google-calendar-v2"
    product_keys = ("calendar",)
    supported_patterns = ("**/*.ics",)
    support_level: SupportLevel = "fully_structured"
    representation_kind = "records+events+relationships"
    is_fallback = False

    def probe(self, member: TakeoutMember) -> MatchResult:
        return MatchResult(
            member.product_key == "calendar"
            and member.extension == ".ics"
            and member.uncompressed_size <= SEMANTIC_MAX_MEMBER_BYTES
            and not member.is_directory,
            "Calendar iCalendar representation",
            requires_byte_level_inspection=True,
        )

    def parse(self, stream: BinaryIO, member: TakeoutMember) -> ParsedExport:
        content = _read_bounded(stream, member)
        try:
            text = content.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as error:
            raise GoogleAdapterError("could not decode Calendar member") from error
        lines = _unfold_lines(text)
        if not any(line.upper() == "BEGIN:VCALENDAR" for line in lines):
            raise GoogleAdapterError("Calendar member is not an iCalendar document")
        calendar_name = next(
            (
                value
                for line in lines
                if (parsed := _property(line)) is not None
                for name, _, value in (parsed,)
                if name == "X-WR-CALNAME"
            ),
            None,
        )
        calendar_value = {
            "name": calendar_name,
            "member_identity": PurePosixPath(member.member_path).name,
        }
        calendar_id = _identity(calendar_value, prefix="calendar")
        authority_at, authority_kind = _authority(member, None)
        records: list[NormalizedRecord] = [
            NormalizedRecord(
                source="google",
                record_type="calendar",
                source_record_namespace="google_takeout/calendar/calendar/ics",
                source_record_id=calendar_id,
                title=calendar_name,
                actor_identity="account_owner",
                metadata={
                    "google": calendar_value,
                    "_pdl": {"takeout_product": "calendar", "semantic_adapter": self.adapter_name},
                },
                authority_at=authority_at,
                authority_kind=authority_kind,
            )
        ]
        events: list[NormalizedEvent] = []
        assets: list[NormalizedAsset] = []
        relationships: list[NormalizedRelationship] = []
        seen: dict[str, int] = {}
        for index, properties in enumerate(_component_properties(lines, "VEVENT")):
            raw = _properties_dict(properties)
            uid_item = _first_property(properties, "UID")
            uid = uid_item[1].strip() if uid_item and uid_item[1].strip() else None
            event_id = _dedupe_identity(
                f"provider:{uid}" if uid else _identity(raw, prefix=str(index)),
                seen,
            )
            summary_item = _first_property(properties, "SUMMARY")
            description_item = _first_property(properties, "DESCRIPTION")
            updated_at = _calendar_datetime(
                _first_property(properties, "LAST-MODIFIED")
                or _first_property(properties, "DTSTAMP")
            )
            start_at = _calendar_datetime(_first_property(properties, "DTSTART"))
            end_at = _calendar_datetime(_first_property(properties, "DTEND"))
            event_authority_at, event_authority_kind = _authority(member, updated_at)
            records.append(
                NormalizedRecord(
                    source="google",
                    record_type="calendar_event_definition",
                    source_record_namespace="google_takeout/calendar/event_definition/ics",
                    source_record_id=event_id,
                    title=summary_item[1] if summary_item else None,
                    text=description_item[1] if description_item else None,
                    updated_at=updated_at,
                    occurred_at=start_at,
                    actor_identity="account_owner",
                    metadata={
                        "google": raw,
                        "_pdl": {
                            "takeout_product": "calendar",
                            "semantic_adapter": self.adapter_name,
                            "end_at": end_at.isoformat() if end_at else None,
                            "recurrence_definition": (
                                _first_property(properties, "RRULE")[1]
                                if _first_property(properties, "RRULE")
                                else None
                            ),
                            "source_order": index,
                        },
                    },
                    provider_updated_at=updated_at,
                    authority_at=event_authority_at,
                    authority_kind=event_authority_kind,
                )
            )
            relationship_id = hashlib.sha256(
                f"{calendar_id}\0{event_id}".encode()
            ).hexdigest()
            relationships.append(
                NormalizedRelationship(
                    source="google",
                    relationship_type="calendar_contains_event_definition",
                    source_relationship_namespace="google_takeout/calendar/contains",
                    source_relationship_id=relationship_id,
                    subject_kind="record",
                    subject_source="google",
                    subject_namespace="google_takeout/calendar/calendar/ics",
                    subject_source_id=calendar_id,
                    object_kind="record",
                    object_source="google",
                    object_namespace="google_takeout/calendar/event_definition/ics",
                    object_source_id=event_id,
                    metadata={"_pdl": {"provider_evidenced": True}},
                    provider_updated_at=updated_at,
                    authority_at=event_authority_at,
                    authority_kind=event_authority_kind,
                )
            )
            attachments = [
                (params, value)
                for name, params, value in properties
                if name == "ATTACH" and value.strip()
            ]
            for attachment_index, (params, value) in enumerate(attachments):
                locator = value.strip()
                parsed_locator = urlparse(locator)
                filename = PurePosixPath(parsed_locator.path).name or "calendar-attachment"
                asset_id = hashlib.sha256(
                    f"{event_id}\0{attachment_index}\0{locator}".encode()
                ).hexdigest()
                asset_namespace = "google_takeout/calendar/attachment/ics"
                assets.append(
                    NormalizedAsset(
                        source="google",
                        asset_namespace=asset_namespace,
                        source_asset_id=asset_id,
                        mime_type=params.get("FMTTYPE"),
                        filename=filename,
                        original_path=(
                            locator
                            if parsed_locator.scheme
                            else f"{member.source_locator}#VEVENT/{event_id}/ATTACH/{attachment_index}"
                        ),
                        size_bytes=0,
                        metadata={
                            "google": {"params": params, "value": locator},
                            "_pdl": {
                                "referenced_by_event_definition": event_id,
                                "external_reference": bool(parsed_locator.scheme),
                            },
                        },
                        provider_updated_at=updated_at,
                        authority_at=event_authority_at,
                        authority_kind=event_authority_kind,
                    )
                )
                attachment_relation_id = hashlib.sha256(
                    f"{event_id}\0{asset_id}\0calendar_attachment".encode()
                ).hexdigest()
                relationships.append(
                    NormalizedRelationship(
                        source="google",
                        relationship_type="calendar_event_definition_has_asset",
                        source_relationship_namespace="google_takeout/calendar/attachment_reference",
                        source_relationship_id=attachment_relation_id,
                        subject_kind="record",
                        subject_source="google",
                        subject_namespace="google_takeout/calendar/event_definition/ics",
                        subject_source_id=event_id,
                        object_kind="asset",
                        object_source="google",
                        object_namespace=asset_namespace,
                        object_source_id=asset_id,
                        metadata={"_pdl": {"provider_evidenced": True}},
                        provider_updated_at=updated_at,
                        authority_at=event_authority_at,
                        authority_kind=event_authority_kind,
                    )
                )
            if start_at is not None and _first_property(properties, "RRULE") is None:
                events.append(
                    NormalizedEvent(
                        occurred_at=start_at,
                        source="google",
                        event_type="calendar_event",
                        source_record_namespace="google_takeout/calendar/event/ics",
                        source_record_id=event_id,
                        actor_identity="account_owner",
                        text=summary_item[1] if summary_item else None,
                        metadata={
                            "google": raw,
                            "_pdl": {
                                "definition_source_id": event_id,
                                "end_at": end_at.isoformat() if end_at else None,
                            },
                        },
                        provider_updated_at=updated_at,
                        authority_at=event_authority_at,
                        authority_kind=event_authority_kind,
                    )
                )
        return _parsed_export(
            member=member,
            content=content,
            parser_version=self.parser_version,
            category="takeout/calendar/semantic/ics",
            records=records,
            events=events,
            assets=assets,
            relationships=relationships,
        )


def _unescape_vcard(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


class ContactsAdapter:
    adapter_name = "contacts_semantic"
    parser_version = "google-contacts-v2"
    product_keys = ("contacts",)
    supported_patterns = ("**/*.vcf", "**/*.csv", "**/*.json")
    support_level: SupportLevel = "fully_structured"
    representation_kind = "records+assets+relationships"
    is_fallback = False

    def probe(self, member: TakeoutMember) -> MatchResult:
        return MatchResult(
            member.product_key == "contacts"
            and member.extension in {".vcf", ".csv", ".json"}
            and member.uncompressed_size <= SEMANTIC_MAX_MEMBER_BYTES
            and not member.is_directory,
            "Contacts vCard or structured representation",
            requires_byte_level_inspection=True,
        )

    def parse(self, stream: BinaryIO, member: TakeoutMember) -> ParsedExport:
        content = _read_bounded(stream, member)
        if member.extension != ".vcf":
            return StructuredProductAdapter(
                adapter_name=self.adapter_name,
                parser_version=self.parser_version,
                product_keys=self.product_keys,
                family="contacts",
                supported_extensions=(".csv", ".json"),
            ).parse(io.BytesIO(content), member)
        try:
            text = content.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as error:
            raise GoogleAdapterError("could not decode Contacts member") from error
        cards = _component_properties(_unfold_lines(text), "VCARD")
        records: list[NormalizedRecord] = []
        assets: list[NormalizedAsset] = []
        relationships: list[NormalizedRelationship] = []
        group_ids: dict[str, str] = {}
        seen: dict[str, int] = {}
        for index, properties in enumerate(cards):
            raw = _properties_dict(properties)
            uid_item = _first_property(properties, "UID")
            uid = uid_item[1].strip() if uid_item and uid_item[1].strip() else None
            contact_id = _dedupe_identity(
                f"provider:{uid}" if uid else _identity(raw, prefix=str(index)),
                seen,
            )
            fn = _first_property(properties, "FN")
            updated_at = _calendar_datetime(_first_property(properties, "REV"))
            authority_at, authority_kind = _authority(member, updated_at)
            records.append(
                NormalizedRecord(
                    source="google",
                    record_type="contact",
                    source_record_namespace="google_takeout/contacts/contact/vcard",
                    source_record_id=contact_id,
                    title=_unescape_vcard(fn[1]) if fn else None,
                    text=None,
                    updated_at=updated_at,
                    actor_identity="account_owner",
                    metadata={
                        "google": raw,
                        "_pdl": {
                            "takeout_product": "contacts",
                            "semantic_adapter": self.adapter_name,
                            "source_order": index,
                        },
                    },
                    provider_updated_at=updated_at,
                    authority_at=authority_at,
                    authority_kind=authority_kind,
                )
            )
            categories = _first_property(properties, "CATEGORIES")
            for category in categories[1].split(",") if categories else ():
                group_name = _unescape_vcard(category).strip()
                if not group_name:
                    continue
                group_id = group_ids.setdefault(
                    group_name,
                    "content:" + hashlib.sha256(group_name.encode("utf-8")).hexdigest(),
                )
                if not any(record.source_record_id == group_id for record in records):
                    records.append(
                        NormalizedRecord(
                            source="google",
                            record_type="contact_group",
                            source_record_namespace="google_takeout/contacts/group/vcard",
                            source_record_id=group_id,
                            title=group_name,
                            actor_identity="account_owner",
                            metadata={"_pdl": {"semantic_adapter": self.adapter_name}},
                            authority_at=authority_at,
                            authority_kind=authority_kind,
                        )
                    )
                relation_id = hashlib.sha256(
                    f"{group_id}\0{contact_id}".encode()
                ).hexdigest()
                relationships.append(
                    NormalizedRelationship(
                        source="google",
                        relationship_type="contact_group_contains_contact",
                        source_relationship_namespace="google_takeout/contacts/group_membership",
                        source_relationship_id=relation_id,
                        subject_kind="record",
                        subject_source="google",
                        subject_namespace="google_takeout/contacts/group/vcard",
                        subject_source_id=group_id,
                        object_kind="record",
                        object_source="google",
                        object_namespace="google_takeout/contacts/contact/vcard",
                        object_source_id=contact_id,
                        metadata={"_pdl": {"provider_evidenced": True}},
                        authority_at=authority_at,
                        authority_kind=authority_kind,
                    )
                )
            photo = _first_property(properties, "PHOTO")
            if photo and photo[1].casefold().startswith(("http://", "https://")):
                photo_url = photo[1]
                parsed_url = urlparse(photo_url)
                filename = PurePosixPath(parsed_url.path).name or "contact-photo"
                asset_id = hashlib.sha256(photo_url.encode("utf-8")).hexdigest()
                assets.append(
                    NormalizedAsset(
                        source="google",
                        asset_namespace="google_takeout/contacts/photo",
                        source_asset_id=asset_id,
                        filename=filename,
                        original_path=photo_url,
                        size_bytes=0,
                        metadata={"_pdl": {"referenced_by_contact": contact_id}},
                        authority_at=authority_at,
                        authority_kind=authority_kind,
                    )
                )
        return _parsed_export(
            member=member,
            content=content,
            parser_version=self.parser_version,
            category="takeout/contacts/semantic/vcard",
            records=records,
            assets=assets,
            relationships=relationships,
        )


def _geo_coordinates(value: object) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    folded = {str(key).casefold().replace("_", ""): item for key, item in value.items()}
    latitude = folded.get("latitude", folded.get("lat"))
    longitude = folded.get("longitude", folded.get("lng", folded.get("lon")))
    try:
        if latitude is not None and longitude is not None:
            lat = float(latitude)
            lon = float(longitude)
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
    except (TypeError, ValueError):
        return None
    location = folded.get("location")
    if isinstance(location, dict):
        return _geo_coordinates(location)
    geometry = folded.get("geometry")
    if isinstance(geometry, dict):
        coordinates = geometry.get("coordinates")
        if (
            isinstance(coordinates, list)
            and len(coordinates) >= 2
            and all(isinstance(item, (int, float)) for item in coordinates[:2])
        ):
            longitude, latitude = float(coordinates[0]), float(coordinates[1])
            if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                return latitude, longitude
    return None


def _decode_kml(content: bytes) -> list[object]:
    text = content.decode("utf-8-sig", errors="strict")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("XML declarations with entities are not supported")
    root = ElementTree.fromstring(text)
    placemarks = root.findall(".//{*}Placemark")
    if not placemarks:
        return [{"xml": _xml_value(root), "raw_xml": text}]
    result: list[object] = []
    for placemark in placemarks:
        name = placemark.findtext("{*}name")
        description = placemark.findtext("{*}description")
        coordinates_text = placemark.findtext(".//{*}coordinates")
        geometry: dict[str, object] | None = None
        if coordinates_text:
            first = coordinates_text.strip().split()[0].split(",")
            try:
                if len(first) >= 2:
                    geometry = {
                        "type": "Point",
                        "coordinates": [float(first[0]), float(first[1])],
                    }
            except ValueError:
                geometry = None
        result.append(
            {
                "name": name,
                "description": description,
                "geometry": geometry,
                "xml": _xml_value(placemark),
            }
        )
    return result


class GeoAdapter:
    adapter_name = "geo_semantic"
    parser_version = "google-geo-v2"
    product_keys = ("timeline", "maps", "maps_your_places", "my_maps", "saved")
    supported_patterns = (
        "**/*.json",
        "**/*.csv",
        "**/*.kml",
        "**/*.kmz",
        "**/*.xml",
        "**/*.txt",
    )
    support_level: SupportLevel = "fully_structured"
    representation_kind = "records+events+relationships"
    is_fallback = False

    def probe(self, member: TakeoutMember) -> MatchResult:
        return MatchResult(
            member.product_key in self.product_keys
            and member.extension in {".json", ".csv", ".kml", ".kmz", ".xml", ".txt"}
            and member.uncompressed_size <= SEMANTIC_MAX_MEMBER_BYTES
            and not member.is_directory,
            "geographic structured representation",
            requires_byte_level_inspection=True,
        )

    def _values(self, content: bytes, member: TakeoutMember) -> list[object]:
        if member.extension == ".kml":
            return _decode_kml(content)
        if member.extension != ".kmz":
            return _decode_structured(content, member.extension)
        try:
            with ZipFile(io.BytesIO(content)) as archive:
                candidates = [name for name in archive.namelist() if name.casefold().endswith(".kml")]
                if len(candidates) != 1:
                    raise GoogleAdapterError("KMZ must contain exactly one KML document")
                info = archive.getinfo(candidates[0])
                if info.file_size > SEMANTIC_MAX_MEMBER_BYTES:
                    raise GoogleAdapterError("KML document exceeds the bounded parser limit")
                return _decode_kml(archive.read(info))
        except (BadZipFile, KeyError, OSError, RuntimeError) as error:
            if isinstance(error, GoogleAdapterError):
                raise
            raise GoogleAdapterError("could not parse KMZ member") from error

    def parse(self, stream: BinaryIO, member: TakeoutMember) -> ParsedExport:
        content = _read_bounded(stream, member)
        try:
            values = self._values(content, member)
        except (csv.Error, ElementTree.ParseError, UnicodeDecodeError, ValueError) as error:
            if isinstance(error, GoogleAdapterError):
                raise
            raise GoogleAdapterError("could not parse geographic member") from error
        product_key = member.product_key
        if product_key is None:
            raise GoogleAdapterError("geo adapter requires a known product")
        representation = member.extension.removeprefix(".")
        provider_container: dict[str, object] | None = None
        if (
            len(values) == 1
            and isinstance(values[0], dict)
            and isinstance(values[0].get("features"), list)
        ):
            feature_collection = values[0]
            provider_container = {
                key: value for key, value in feature_collection.items() if key != "features"
            }
            values = list(feature_collection["features"])
        records: list[NormalizedRecord] = []
        events: list[NormalizedEvent] = []
        relationships: list[NormalizedRelationship] = []
        seen: dict[str, int] = {}
        container_kind = "saved_list" if product_key == "saved" else "map"
        container_namespace = f"google_takeout/{product_key}/{container_kind}/{representation}"
        container_value = {
            "product": product_key,
            "member": member.member_path,
            "representation": representation,
        }
        container_id = _identity(container_value, prefix="container")
        authority_at, authority_kind = _authority(member, None)
        if product_key in {"saved", "my_maps"}:
            records.append(
                NormalizedRecord(
                    source="google",
                    record_type=container_kind,
                    source_record_namespace=container_namespace,
                    source_record_id=container_id,
                    title=PurePosixPath(member.member_path).stem,
                    actor_identity="account_owner",
                    metadata={
                        "google": container_value,
                        "_pdl": {"semantic_adapter": self.adapter_name},
                    },
                    authority_at=authority_at,
                    authority_kind=authority_kind,
                )
            )
        for index, value in enumerate(values):
            item_id = _dedupe_identity(_identity(value, prefix=str(index)), seen)
            updated_at = _time(value, ("updated", "modified", "lastModified"))
            occurred_at = _time(value, ("time", "timestamp", "startTime", "date"))
            item_authority_at, item_authority_kind = _authority(member, updated_at)
            coordinates = _geo_coordinates(value)
            geo = (
                {
                    "latitude": coordinates[0],
                    "longitude": coordinates[1],
                    "source_precision": "provider_value",
                }
                if coordinates
                else None
            )
            if product_key == "timeline" and occurred_at is not None:
                event_namespace = f"google_takeout/{product_key}/event/{representation}"
                events.append(
                    NormalizedEvent(
                        occurred_at=occurred_at,
                        source="google",
                        event_type="timeline_event",
                        source_record_namespace=event_namespace,
                        source_record_id=item_id,
                        actor_identity="account_owner",
                        text=_text_projection(value),
                        metadata={
                            "google": value,
                            "_pdl": {
                                "takeout_product": product_key,
                                "semantic_adapter": self.adapter_name,
                                "geo": geo,
                                "provider_container": provider_container,
                                "source_order": index,
                            },
                        },
                        provider_updated_at=updated_at,
                        authority_at=item_authority_at,
                        authority_kind=item_authority_kind,
                    )
                )
                continue
            if product_key == "saved":
                record_type = "saved_item"
            elif product_key == "timeline" and isinstance(value, dict) and any(
                key in value
                for key in (
                    "timelineEnabled",
                    "deviceSettings",
                    "retentionControl",
                    "latestTimelineSettingChange",
                )
            ):
                record_type = "timeline_settings"
            elif product_key == "timeline" and member.extension == ".txt":
                record_type = "timeline_notice"
            else:
                record_type = "place"
            item_namespace = f"google_takeout/{product_key}/{record_type}/{representation}"
            records.append(
                NormalizedRecord(
                    source="google",
                    record_type=record_type,
                    source_record_namespace=item_namespace,
                    source_record_id=item_id,
                    title=_first_string(value, ("title", "name", "label", "address")),
                    text=_text_projection(value),
                    updated_at=updated_at,
                    occurred_at=occurred_at,
                    actor_identity="account_owner",
                    metadata={
                        "google": value,
                        "_pdl": {
                            "takeout_product": product_key,
                            "semantic_adapter": self.adapter_name,
                            "geo": geo,
                            "provider_container": provider_container,
                            "source_order": index,
                        },
                    },
                    provider_updated_at=updated_at,
                    authority_at=item_authority_at,
                    authority_kind=item_authority_kind,
                )
            )
            if product_key in {"saved", "my_maps"}:
                relationship_type = (
                    "saved_list_contains_item" if product_key == "saved" else "map_contains_place"
                )
                relation_id = hashlib.sha256(
                    f"{container_id}\0{item_id}\0{relationship_type}".encode()
                ).hexdigest()
                relationships.append(
                    NormalizedRelationship(
                        source="google",
                        relationship_type=relationship_type,
                        source_relationship_namespace=f"google_takeout/{product_key}/contains",
                        source_relationship_id=relation_id,
                        subject_kind="record",
                        subject_source="google",
                        subject_namespace=container_namespace,
                        subject_source_id=container_id,
                        object_kind="record",
                        object_source="google",
                        object_namespace=item_namespace,
                        object_source_id=item_id,
                        metadata={"_pdl": {"provider_evidenced": True, "source_order": index}},
                        provider_updated_at=updated_at,
                        authority_at=item_authority_at,
                        authority_kind=item_authority_kind,
                    )
                )
        return _parsed_export(
            member=member,
            content=content,
            parser_version=self.parser_version,
            category=f"takeout/{product_key}/semantic/{representation}",
            records=records,
            events=events,
            relationships=relationships,
        )


class TasksAdapter:
    adapter_name = "tasks_semantic"
    parser_version = "google-tasks-v2"
    product_keys = ("tasks",)
    supported_patterns = ("**/*.json", "**/*.csv")
    support_level: SupportLevel = "fully_structured"
    representation_kind = "records+relationships"
    is_fallback = False

    def probe(self, member: TakeoutMember) -> MatchResult:
        return MatchResult(
            member.product_key == "tasks"
            and member.extension in {".json", ".csv"}
            and member.uncompressed_size <= SEMANTIC_MAX_MEMBER_BYTES
            and not member.is_directory,
            "Tasks structured representation",
            requires_byte_level_inspection=True,
        )

    def parse(self, stream: BinaryIO, member: TakeoutMember) -> ParsedExport:
        content = _read_bounded(stream, member)
        try:
            decoded = _decode_structured(content, member.extension)
        except (csv.Error, UnicodeDecodeError, ValueError) as error:
            raise GoogleAdapterError("could not parse Tasks member") from error
        records: list[NormalizedRecord] = []
        relationships: list[NormalizedRelationship] = []
        representation = member.extension.removeprefix(".")
        authority_at, authority_kind = _authority(member, None)
        containers = decoded
        if len(decoded) == 1 and isinstance(decoded[0], dict):
            root = decoded[0]
            nested = next(
                (root[key] for key in ("taskLists", "lists", "items") if isinstance(root.get(key), list)),
                None,
            )
            if nested is not None:
                containers = list(nested)
        list_seen: dict[str, int] = {}
        task_seen: dict[str, int] = {}
        for list_index, container in enumerate(containers):
            list_value = container if isinstance(container, dict) else {"value": container}
            list_id = _dedupe_identity(
                _identity(list_value, prefix=f"list-{list_index}"),
                list_seen,
            )
            list_namespace = f"google_takeout/tasks/task_list/{representation}"
            records.append(
                NormalizedRecord(
                    source="google",
                    record_type="task_list",
                    source_record_namespace=list_namespace,
                    source_record_id=list_id,
                    title=_first_string(list_value, ("title", "name", "label")),
                    actor_identity="account_owner",
                    metadata={
                        "google": list_value,
                        "_pdl": {"semantic_adapter": self.adapter_name, "source_order": list_index},
                    },
                    authority_at=authority_at,
                    authority_kind=authority_kind,
                )
            )
            nested_tasks = next(
                (
                    list_value[key]
                    for key in ("tasks", "items", "children")
                    if isinstance(list_value.get(key), list)
                ),
                None,
            )
            provider_kind = _first_string(list_value, ("kind", "type")) or ""
            is_list_container = nested_tasks is not None or "tasklist" in provider_kind.casefold()
            task_values = (
                list(nested_tasks)
                if nested_tasks is not None
                else []
                if is_list_container
                else [list_value]
            )
            for task_index, task in enumerate(task_values):
                task_value = task if isinstance(task, dict) else {"value": task}
                task_id = _dedupe_identity(
                    _identity(task_value, prefix=f"task-{list_index}-{task_index}"),
                    task_seen,
                )
                updated_at = _time(task_value, ("updated", "modified"))
                completed_at = _time(task_value, ("completed", "completedAt"))
                task_authority_at, task_authority_kind = _authority(member, updated_at)
                task_namespace = f"google_takeout/tasks/task/{representation}"
                records.append(
                    NormalizedRecord(
                        source="google",
                        record_type="task",
                        source_record_namespace=task_namespace,
                        source_record_id=task_id,
                        title=_first_string(task_value, ("title", "name")),
                        text=_first_string(task_value, ("notes", "description", "text")),
                        updated_at=updated_at,
                        occurred_at=completed_at,
                        actor_identity="account_owner",
                        metadata={
                            "google": task_value,
                            "_pdl": {
                                "semantic_adapter": self.adapter_name,
                                "source_order": task_index,
                                "completion_is_provider_evidenced": completed_at is not None,
                            },
                        },
                        provider_updated_at=updated_at,
                        authority_at=task_authority_at,
                        authority_kind=task_authority_kind,
                    )
                )
                relation_type = "task_list_contains_task"
                relation_id = hashlib.sha256(
                    f"{list_id}\0{task_id}\0{relation_type}".encode()
                ).hexdigest()
                relationships.append(
                    NormalizedRelationship(
                        source="google",
                        relationship_type=relation_type,
                        source_relationship_namespace="google_takeout/tasks/list_membership",
                        source_relationship_id=relation_id,
                        subject_kind="record",
                        subject_source="google",
                        subject_namespace=list_namespace,
                        subject_source_id=list_id,
                        object_kind="record",
                        object_source="google",
                        object_namespace=task_namespace,
                        object_source_id=task_id,
                        metadata={"_pdl": {"provider_evidenced": True}},
                        provider_updated_at=updated_at,
                        authority_at=task_authority_at,
                        authority_kind=task_authority_kind,
                    )
                )
                parent_id = _first_string(task_value, ("parent", "parentId"))
                if parent_id:
                    parent_relation_id = hashlib.sha256(
                        f"{parent_id}\0{task_id}\0task_parent".encode()
                    ).hexdigest()
                    relationships.append(
                        NormalizedRelationship(
                            source="google",
                            relationship_type="task_parent_contains_child",
                            source_relationship_namespace="google_takeout/tasks/parent_child",
                            source_relationship_id=parent_relation_id,
                            subject_kind="record",
                            subject_source="google",
                            subject_namespace=task_namespace,
                            subject_source_id=f"provider:{parent_id}",
                            object_kind="record",
                            object_source="google",
                            object_namespace=task_namespace,
                            object_source_id=task_id,
                            metadata={"_pdl": {"provider_evidenced": True}},
                            provider_updated_at=updated_at,
                            authority_at=task_authority_at,
                            authority_kind=task_authority_kind,
                        )
                    )
        return _parsed_export(
            member=member,
            content=content,
            parser_version=self.parser_version,
            category=f"takeout/tasks/semantic/{representation}",
            records=records,
            relationships=relationships,
        )


PR6_ADAPTERS = (GeoAdapter(), CalendarAdapter(), ContactsAdapter(), TasksAdapter())
