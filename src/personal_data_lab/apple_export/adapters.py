from __future__ import annotations

import csv
import hashlib
import io
import json
import mimetypes
import re
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol, runtime_checkable
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..fingerprints import canonical_json_bytes
from ..models import (
    NormalizedAsset,
    NormalizedEvent,
    NormalizedRecord,
    NormalizedRelationship,
    ParsedExport,
)
from .inventory import AppleMember, open_apple_member

CSV_BATCH_ROWS = 5_000
MAX_TEXT_BYTES = 64 * 1024 * 1024
PARSER_VERSION = "apple-privacy-v3"


class AppleAdapterError(ValueError):
    """Raised when a classified Apple member cannot be represented safely."""


@runtime_checkable
class AppleAdapter(Protocol):
    adapter_name: str
    parser_version: str
    support_level: str
    interpretation_level: str

    def parse(self, stream: BinaryIO, member: AppleMember) -> ParsedExport: ...


def _stream_sha256(member: AppleMember) -> str:
    digest = hashlib.sha256()
    with open_apple_member(member) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_token(member: AppleMember, *, parent: bool = False) -> str:
    path = PurePosixPath(member.member_path)
    if parent:
        path = path.parent
    payload = canonical_json_bytes(
        {
            "containers": list(
                zip(member.container_chain, member.container_occurrences, strict=True)
            ),
            "path": path.as_posix(),
            "occurrence": 0 if parent else member.entry_occurrence,
        }
    )
    return hashlib.sha256(payload).hexdigest()[:24]


def _dataset_key(member: AppleMember) -> str:
    stem = Path(member.filename).stem.casefold()
    stem = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    if member.product_key in {"notes", "contacts"} and stem:
        return member.product_key
    return stem[:80] or "data"


def _namespace(member: AppleMember, kind: str) -> str:
    return f"apple_privacy/{member.product_key}/{_dataset_key(member)}/{kind}"


def _note_container(member: AppleMember) -> NormalizedRecord:
    parent = PurePosixPath(member.member_path).parent
    return NormalizedRecord(
        source="apple",
        record_type="note_container",
        source_record_namespace="apple_privacy/notes/container/record",
        source_record_id=f"path:{_path_token(member, parent=True)}",
        title=parent.name or None,
        metadata={
            "apple": {"directory_name": parent.name},
            "_pdl": {
                "acquisition_method": "apple_privacy_export",
                "adapter": "apple_notes",
                "product": "notes",
                "semantic_kind": "note_container",
            },
        },
        authority_kind="unknown",
    )


def _metadata(
    member: AppleMember, value: object, adapter: str, **extra: object
) -> dict[str, object]:
    return {
        "apple": value,
        "_pdl": {
            "acquisition_method": "apple_privacy_export",
            "adapter": adapter,
            "product": member.product_key,
            "dataset": _dataset_key(member),
            **extra,
        },
    }


def _json_safe(value: object) -> object:
    """Keep provider content JSON-compatible without dropping irregular CSV columns."""

    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            normalized = "_extra_columns" if key is None else str(key)
            if normalized in result:
                existing = result[normalized]
                result[normalized] = [existing, _json_safe(item)]
            else:
                result[normalized] = _json_safe(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _timestamp(value: object) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        try:
            return datetime.fromtimestamp(number, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(normalized)
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result
    except ValueError:
        pass
    for pattern in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S %z",
        "%Y/%m/%d %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y",
        "%Y-%m-%d",
        "%Y%m%dT%H%M%SZ",
        "%Y%m%d",
    ):
        try:
            result = datetime.strptime(text, pattern)  # noqa: DTZ007 - normalized immediately
            return result.replace(tzinfo=UTC) if result.tzinfo is None else result
        except ValueError:
            continue
    return None


def _first_value(value: dict[str, object], names: tuple[str, ...]) -> object | None:
    folded = {str(key).casefold().replace("_", " "): item for key, item in value.items()}
    for name in names:
        result = folded.get(name.casefold().replace("_", " "))
        if result not in (None, "", [], {}):
            return result
    return None


# Only explicit dataset-level entity identifiers belong here. A device/account ID in
# activity history is not an event ID. Unmapped datasets use complete payload identity.
_DATASET_ID_FIELDS = {
    ("account_device", "devices"): ("device identifier",),
}
_TIME_NAMES = (
    "event date time",
    "event time",
    "event date",
    "transaction date",
    "activity date",
    "created date",
    "creation date",
    "date created",
    "last modified",
    "updated date",
    "start date",
    "date",
    "timestamp",
    "taggedon",
)
_TITLE_NAMES = (
    "title",
    "name",
    "description",
    "item description",
    "track description",
    "content title",
    "app name",
    "application",
    "event type",
    "action type",
    "transaction type",
    "notification type",
)
_TEXT_NAMES = (
    "text",
    "body",
    "content",
    "review text",
    "details",
    "description",
    "message",
    "note",
)


def _string(value: object | None) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    result = str(value).strip()
    return result or None


def _identity(value: object, occurrence: int = 1, *, id_names: tuple[str, ...] = ()) -> str:
    provider_id = None
    if isinstance(value, dict):
        provider_id = _string(_first_value(value, id_names))
    if provider_id:
        base = f"provider:{hashlib.sha256(provider_id.encode()).hexdigest()[:32]}"
    else:
        base = f"content:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"
    return base if occurrence == 1 else f"{base}#occurrence={occurrence}"


def _semantic_kind(member: AppleMember, value: dict[str, object]) -> tuple[str, bool]:
    dataset = _dataset_key(member)
    evidence = " ".join((dataset, *[str(key).casefold() for key in value]))
    event_words = (
        "activity",
        "event",
        "history",
        "transaction",
        "notification",
        "visit",
        "session",
        "install",
        "play",
        "download",
        "delivery",
        "response",
        "purchase",
        "vote",
        "signon",
    )
    is_event = (
        any(word in evidence for word in event_words)
        and _first_value(value, _TIME_NAMES) is not None
    )
    return f"apple_{member.product_key}_{dataset}", is_event


def _objects(
    member: AppleMember,
    values: list[object],
    *,
    adapter: str,
    seen: defaultdict[str, int] | None = None,
) -> tuple[tuple[NormalizedRecord, ...], tuple[NormalizedEvent, ...]]:
    occurrences: defaultdict[str, int] = seen if seen is not None else defaultdict(int)
    records: list[NormalizedRecord] = []
    events: list[NormalizedEvent] = []
    id_names = _DATASET_ID_FIELDS.get((member.product_key, _dataset_key(member)), ())
    for raw in values:
        safe = _json_safe(raw)
        value = safe if isinstance(safe, dict) else {"value": safe}
        base = _identity(value, id_names=id_names)
        occurrences[base] += 1
        if base.startswith("provider:") and occurrences[base] > 1:
            raise AppleAdapterError("duplicate entity identifier in mapped Apple dataset")
        source_id = _identity(value, occurrences[base], id_names=id_names)
        record_type, is_event = _semantic_kind(member, value)
        occurred_at = _timestamp(_first_value(value, _TIME_NAMES))
        title = _string(_first_value(value, _TITLE_NAMES))
        text = _string(_first_value(value, _TEXT_NAMES))
        records.append(
            NormalizedRecord(
                source="apple",
                record_type=record_type,
                source_record_namespace=_namespace(member, "record"),
                source_record_id=source_id,
                title=title,
                text=text,
                occurred_at=occurred_at,
                metadata=_metadata(member, value, adapter, semantic_kind=record_type),
                authority_kind="unknown",
            )
        )
        if is_event:
            events.append(
                NormalizedEvent(
                    occurred_at=occurred_at,
                    source="apple",
                    event_type=record_type.removeprefix("apple_"),
                    source_record_namespace=_namespace(member, "event"),
                    source_record_id=source_id,
                    text=text or title,
                    metadata=_metadata(member, value, adapter, semantic_kind=record_type),
                    authority_kind="unknown",
                )
            )
    return tuple(records), tuple(events)


def _export(
    member: AppleMember,
    sha256: str,
    *,
    adapter: str,
    records: tuple[NormalizedRecord, ...] = (),
    events: tuple[NormalizedEvent, ...] = (),
    assets: tuple[NormalizedAsset, ...] = (),
    relationships: tuple[NormalizedRelationship, ...] = (),
    warnings: tuple[str, ...] = (),
    chunk: int | None = None,
) -> ParsedExport:
    # Parsing can depend on the note directory even when source bytes are identical.
    # Stable member context excludes archive filename and unrelated ZIP entry positions.
    category = f"apple_privacy/{member.product_key}/{_dataset_key(member)}/{_path_token(member)}"
    if chunk is not None:
        category += f"/chunk/{chunk:08d}"
    return ParsedExport(
        source="apple",
        parser_version=f"{PARSER_VERSION}/{adapter}",
        category=category,
        filename=member.filename,
        original_path=member.source_locator,
        sha256=sha256,
        size_bytes=member.uncompressed_size,
        snapshot_kind="partial",
        snapshot_scope=f"account/apple_privacy/{member.product_key}",
        parse_warnings=warnings,
        records=records,
        events=events,
        assets=assets,
        relationships=relationships,
    )


class StructuredAdapter:
    """Losslessly normalize Apple CSV, JSON, and XML without inventing a schema.

    This is a production representation contract, not a claim of product-specific
    interpretation. CSV cells (including duplicate headers and surplus columns),
    complete JSON values, and complete XML trees are retained in provider metadata.
    Unknown file formats never reach this adapter and malformed documents fail closed.
    """

    support_level = "losslessly_structured"
    interpretation_level = "generic_lossless"
    adapter_name = "apple_structured"
    parser_version = f"{PARSER_VERSION}/structured"

    def parse(self, stream: BinaryIO, member: AppleMember) -> ParsedExport:
        content = stream.read(MAX_TEXT_BYTES + 1)
        if len(content) > MAX_TEXT_BYTES:
            raise AppleAdapterError("bounded structured parser limit exceeded")
        sha256 = hashlib.sha256(content).hexdigest()
        try:
            text = self._decode_text(content)
            if member.extension == ".json":
                documents = self._json_documents(text)
                values = [
                    item
                    for decoded in documents
                    for item in (decoded if isinstance(decoded, list) else [decoded])
                ]
            elif member.extension == ".xml":
                root = ElementTree.fromstring(
                    text,
                    parser=ElementTree.XMLParser(
                        target=ElementTree.TreeBuilder(insert_comments=True, insert_pis=True)
                    ),
                )
                values = [self._xml_value(root)]
            else:
                raise AppleAdapterError("streaming CSV path required")
        except (UnicodeDecodeError, json.JSONDecodeError, ElementTree.ParseError) as error:
            raise AppleAdapterError("malformed structured Apple member") from error
        records, events = _objects(member, values, adapter=self.adapter_name)
        return _export(member, sha256, adapter=self.adapter_name, records=records, events=events)

    @staticmethod
    def _reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant {value}")

    @staticmethod
    def _decode_text(content: bytes) -> str:
        if content.startswith((b"\xff\xfe", b"\xfe\xff")):
            return content.decode("utf-16")
        if content.startswith(b"\xef\xbb\xbf"):
            return content.decode("utf-8-sig")
        if b"\x00" in content[:128]:
            for encoding in ("utf-16-le", "utf-16-be"):
                try:
                    return content.decode(encoding)
                except UnicodeDecodeError:
                    continue
        return content.decode("utf-8-sig")

    @classmethod
    def _json_documents(cls, text: str) -> list[object]:
        """Decode one JSON value or Apple's observed concatenated-value variant."""

        decoder = json.JSONDecoder(parse_constant=cls._reject_constant)
        values: list[object] = []
        position = 0
        while position < len(text):
            while position < len(text) and text[position].isspace():
                position += 1
            if position >= len(text):
                break
            value, position = decoder.raw_decode(text, position)
            values.append(value)
        return values

    @classmethod
    def _xml_value(cls, element: ElementTree.Element) -> dict[str, object]:
        return {
            "tag": (
                element.tag
                if isinstance(element.tag, str)
                else "#comment"
                if element.tag is ElementTree.Comment
                else "#processing-instruction"
            ),
            "attributes": dict(element.attrib),
            "text": element.text,
            "tail": element.tail,
            "children": [cls._xml_value(child) for child in element],
        }

    def iter_parsed_exports(self, member: AppleMember) -> Iterator[ParsedExport]:
        if member.extension != ".csv":
            with open_apple_member(member) as stream:
                yield self.parse(stream, member)
            return
        sha256 = _stream_sha256(member)
        seen: defaultdict[str, int] = defaultdict(int)
        chunk_index = 0
        with open_apple_member(member) as source:
            text_stream = io.TextIOWrapper(
                source, encoding="utf-8-sig", errors="strict", newline=""
            )
            try:
                sample = text_stream.read(8192)
                text_stream.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
                except csv.Error:
                    dialect = csv.excel
                reader = csv.reader(text_stream, dialect=dialect, strict=True)
                # Apple emits newline-only CSVs for valid empty datasets (observed in
                # TestFlight). Skip leading blank rows and retain a wholly blank file as
                # one empty parsed export so its source identity and successful
                # observation are not lost.
                headers = next((cells for cells in reader if cells), [])
                rows: list[object] = []
                for cells in reader:
                    if not cells:
                        continue
                    if len(set(headers)) != len(headers) or "_extra_columns" in headers:
                        # Positional form avoids duplicate/irregular header overwrites.
                        row = {"headers": headers, "cells": cells}
                    else:
                        row = {
                            key: cells[index] if index < len(cells) else None
                            for index, key in enumerate(headers)
                        }
                        if len(cells) > len(headers):
                            row["_extra_columns"] = cells[len(headers) :]
                    rows.append(row)
                    if len(rows) >= CSV_BATCH_ROWS:
                        chunk_index += 1
                        records, events = _objects(
                            member, rows, adapter=self.adapter_name, seen=seen
                        )
                        yield _export(
                            member,
                            sha256,
                            adapter=self.adapter_name,
                            records=records,
                            events=events,
                            chunk=chunk_index,
                        )
                        rows.clear()
                if rows or chunk_index == 0:
                    chunk_index += 1
                    records, events = _objects(member, rows, adapter=self.adapter_name, seen=seen)
                    yield _export(
                        member,
                        sha256,
                        adapter=self.adapter_name,
                        records=records,
                        events=events,
                        chunk=chunk_index,
                    )
            except (UnicodeDecodeError, csv.Error) as error:
                raise AppleAdapterError("malformed Apple CSV member") from error


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _components(text: str, name: str) -> list[list[str]]:
    """Validate every boundary, retaining nested component lines without flattening them."""
    result: list[list[str]] = []
    stack: list[str] = []
    current: list[str] | None = None
    target_depth = 0
    saw_component = False
    for line in _unfold(text):
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("BEGIN:"):
            component = upper[6:]
            saw_component = True
            if current is not None:
                current.append(line)
            stack.append(component)
            if component == name and current is None:
                current, target_depth = [], len(stack)
        elif upper.startswith("END:"):
            if not stack or stack[-1] != upper[4:]:
                raise AppleAdapterError("unbalanced Apple component boundaries")
            if current is not None:
                if len(stack) == target_depth:
                    result.append(current)
                    current = None
                else:
                    current.append(line)
            stack.pop()
        else:
            if not stack or ":" not in line:
                raise AppleAdapterError("malformed Apple component property")
            if current is not None:
                current.append(line)
    if stack or not saw_component:
        raise AppleAdapterError("incomplete Apple component document")
    return result


def _property_details(lines: list[str]) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = defaultdict(list)
    depth = 0
    for line in lines:
        upper = line.upper()
        if upper.startswith("BEGIN:"):
            depth += 1
            continue
        if upper.startswith("END:"):
            depth -= 1
            continue
        if depth:
            continue
        # Quoted parameter values may contain delimiters.
        parts = re.split(r":(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", line, maxsplit=1)
        if len(parts) != 2:
            raise AppleAdapterError("malformed Apple property")
        head, value = parts
        fields = re.split(r";(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", head)
        params: dict[str, list[str]] = defaultdict(list)
        for field in fields[1:]:
            key, separator, item = field.partition("=")
            if not separator:
                key, item = "TYPE", key
            params[key.upper()].append(item.strip('"'))
        result[fields[0].upper()].append({"value": value, "params": dict(params)})
    return dict(result)


def _properties(lines: list[str]) -> dict[str, list[str]]:
    return {
        key: [str(item["value"]) for item in items]
        for key, items in _property_details(lines).items()
    }


def _calendar_time(lines: list[str], warnings: list[str]) -> datetime | None:
    details = _property_details(lines)
    values = details.get("DTSTART") or details.get("DUE")
    if not values:
        return None
    item = values[0]
    value, params = str(item["value"]), item["params"]
    try:
        if re.fullmatch(r"\d{8}", value):
            datetime.strptime(value, "%Y%m%d")  # noqa: DTZ007 - validate a date, not an instant
            return None  # An all-day date is not a UTC instant.
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        local = datetime.strptime(value, "%Y%m%dT%H%M%S")  # noqa: DTZ007 - zone resolved below
        timezone = params.get("TZID", [])
        if not timezone:
            return None  # Preserve floating local time without inventing a zone.
        zone = ZoneInfo(timezone[0])
        # RFC 5545 selects the first occurrence for repeated local times and the
        # pre-transition offset for nonexistent local times (fold=0).
        return local.replace(tzinfo=zone, fold=0).astimezone(UTC)
    except (ValueError, ZoneInfoNotFoundError):
        warnings.append("unresolved_calendar_time")
        return None


class ContactsAdapter:
    support_level = "fully_structured"
    interpretation_level = "provider_specific"
    adapter_name = "apple_contacts"
    parser_version = f"{PARSER_VERSION}/contacts"

    def parse(self, stream: BinaryIO, member: AppleMember) -> ParsedExport:
        content = stream.read(MAX_TEXT_BYTES + 1)
        if len(content) > MAX_TEXT_BYTES:
            raise AppleAdapterError("contact member exceeds bounded parser limit")
        sha256 = hashlib.sha256(content).hexdigest()
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise AppleAdapterError("malformed Apple vCard") from error
        cards = _components(text, "VCARD")
        if not cards:
            raise AppleAdapterError("Apple vCard document contains no cards")
        records: list[NormalizedRecord] = []
        seen: defaultdict[str, int] = defaultdict(int)
        payloads: dict[str, bytes] = {}
        for lines in cards:
            props = _properties(lines)
            uid = (props.get("UID") or [None])[0]
            fn = (props.get("FN") or [None])[0]
            payload = {
                "properties": props,
                "property_details": _property_details(lines),
                "lines": lines,
            }
            base = (
                f"provider:{uid}"
                if uid
                else f"content:{hashlib.sha256(canonical_json_bytes(payload)).hexdigest()}"
            )
            digest = canonical_json_bytes(payload)
            if base in payloads and payloads[base] != digest:
                raise AppleAdapterError("ambiguous repeated Apple contact UID")
            payloads[base] = digest
            seen[base] += 1
            source_id = base if seen[base] == 1 else f"{base}#occurrence={seen[base]}"
            records.append(
                NormalizedRecord(
                    source="apple",
                    record_type="contact_group" if "Groups/" in member.member_path else "contact",
                    source_record_namespace=_namespace(member, "record"),
                    source_record_id=source_id,
                    title=fn,
                    metadata=_metadata(member, payload, self.adapter_name),
                    authority_kind="unknown",
                )
            )
        return _export(member, sha256, adapter=self.adapter_name, records=tuple(records))


class CalendarAdapter:
    support_level = "fully_structured"
    interpretation_level = "provider_specific"
    adapter_name = "apple_calendar"
    parser_version = f"{PARSER_VERSION}/calendar"

    def parse(self, stream: BinaryIO, member: AppleMember) -> ParsedExport:
        content = stream.read(MAX_TEXT_BYTES + 1)
        if len(content) > MAX_TEXT_BYTES:
            raise AppleAdapterError("calendar member exceeds bounded parser limit")
        sha256 = hashlib.sha256(content).hexdigest()
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise AppleAdapterError("malformed Apple calendar") from error
        records: list[NormalizedRecord] = []
        events: list[NormalizedEvent] = []
        seen: defaultdict[str, int] = defaultdict(int)
        calendars = _components(text, "VCALENDAR")
        if len(calendars) != 1:
            raise AppleAdapterError("Apple calendar requires one VCALENDAR envelope")
        calendar_details = _property_details(calendars[0])
        timezones = _components(text, "VTIMEZONE")
        warnings: list[str] = []
        payloads: dict[str, bytes] = {}
        for component in ("VEVENT", "VTODO"):
            for lines in _components(text, component):
                props = _properties(lines)
                uid = (props.get("UID") or [None])[0]
                details = _property_details(lines)
                payload = {
                    "component": component,
                    "properties": props,
                    "property_details": details,
                    "lines": lines,
                    "calendar_properties": calendar_details,
                    "timezones": timezones,
                }
                recurrence = hashlib.sha256(
                    canonical_json_bytes(details.get("RECURRENCE-ID", []))
                ).hexdigest()
                base = (
                    f"provider:{component}:{uid}:recurrence:{recurrence}"
                    if uid
                    else f"content:{hashlib.sha256(canonical_json_bytes(payload)).hexdigest()}"
                )
                digest = canonical_json_bytes(payload)
                if base in payloads and payloads[base] != digest:
                    raise AppleAdapterError("ambiguous repeated Apple calendar UID/recurrence")
                payloads[base] = digest
                seen[base] += 1
                source_id = base if seen[base] == 1 else f"{base}#occurrence={seen[base]}"
                occurred_at = _calendar_time(lines, warnings)
                title = (props.get("SUMMARY") or [None])[0]
                record_type = "calendar_event" if component == "VEVENT" else "reminder"
                records.append(
                    NormalizedRecord(
                        source="apple",
                        record_type=record_type,
                        source_record_namespace=_namespace(member, "record"),
                        source_record_id=source_id,
                        title=title,
                        occurred_at=occurred_at,
                        metadata=_metadata(
                            member,
                            payload,
                            self.adapter_name,
                        ),
                        authority_kind="unknown",
                    )
                )
                events.append(
                    NormalizedEvent(
                        occurred_at=occurred_at,
                        source="apple",
                        event_type=record_type,
                        source_record_namespace=_namespace(member, "event"),
                        source_record_id=source_id,
                        text=title,
                        metadata=_metadata(
                            member,
                            payload,
                            self.adapter_name,
                        ),
                        authority_kind="unknown",
                    )
                )
        return _export(
            member,
            sha256,
            adapter=self.adapter_name,
            records=tuple(records),
            events=tuple(events),
            warnings=tuple(sorted(set(warnings))),
        )


class NotesAdapter:
    support_level = "fully_structured"
    interpretation_level = "provider_specific"
    adapter_name = "apple_notes"
    parser_version = f"{PARSER_VERSION}/notes"

    def parse(self, stream: BinaryIO, member: AppleMember) -> ParsedExport:
        content = stream.read(MAX_TEXT_BYTES + 1)
        if len(content) > MAX_TEXT_BYTES:
            raise AppleAdapterError("note member exceeds bounded parser limit")
        sha256 = hashlib.sha256(content).hexdigest()
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise AppleAdapterError("malformed Apple note text") from error
        container = _note_container(member)
        note_id = f"path:{_path_token(member)}"
        record = NormalizedRecord(
            source="apple",
            record_type="note",
            source_record_namespace="apple_privacy/notes/note/record",
            source_record_id=note_id,
            title=PurePosixPath(member.member_path).parent.name or Path(member.filename).stem,
            text=text,
            metadata=_metadata(member, {"format": "plain_text"}, self.adapter_name),
            authority_kind="unknown",
        )
        relationship = NormalizedRelationship(
            source="apple",
            relationship_type="note_container_contains_note",
            source_relationship_namespace="apple_privacy/notes/container_note/relationship",
            source_relationship_id=hashlib.sha256(
                f"{container.source_record_id}\x00{note_id}".encode()
            ).hexdigest(),
            subject_kind="record",
            subject_source="apple",
            subject_namespace=container.source_record_namespace,
            subject_source_id=container.source_record_id,
            object_kind="record",
            object_source="apple",
            object_namespace=record.source_record_namespace,
            object_source_id=record.source_record_id,
            metadata={"_pdl": {"provider_evidence": "shared_note_directory"}},
            authority_kind="unknown",
        )
        return _export(
            member,
            sha256,
            adapter=self.adapter_name,
            records=(container, record),
            relationships=(relationship,),
        )


class TextAdapter:
    support_level = "fully_structured"
    interpretation_level = "provider_specific"
    adapter_name = "apple_text"
    parser_version = f"{PARSER_VERSION}/text"

    def parse(self, stream: BinaryIO, member: AppleMember) -> ParsedExport:
        content = stream.read(MAX_TEXT_BYTES + 1)
        if len(content) > MAX_TEXT_BYTES:
            raise AppleAdapterError("text member exceeds bounded parser limit")
        sha256 = hashlib.sha256(content).hexdigest()
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise AppleAdapterError("malformed Apple text member") from error
        record = NormalizedRecord(
            source="apple",
            record_type=f"apple_{member.product_key}_{_dataset_key(member)}",
            source_record_namespace=_namespace(member, "record"),
            source_record_id=f"path:{_path_token(member)}",
            title=Path(member.filename).stem,
            text=text,
            metadata=_metadata(member, {"format": "plain_text"}, self.adapter_name),
            authority_kind="unknown",
        )
        return _export(member, sha256, adapter=self.adapter_name, records=(record,))


class AssetAdapter:
    support_level = "fully_structured"
    interpretation_level = "provider_specific"
    adapter_name = "apple_asset"
    parser_version = f"{PARSER_VERSION}/asset"

    def parse(self, stream: BinaryIO, member: AppleMember) -> ParsedExport:
        digest = hashlib.sha256()
        size = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
        if size != member.uncompressed_size:
            raise AppleAdapterError("asset byte count does not match ZIP central directory")
        sha256 = digest.hexdigest()
        source_id = f"sha256:{sha256}"
        asset = NormalizedAsset(
            source="apple",
            asset_namespace=f"apple_privacy/{member.product_key}/asset",
            source_asset_id=source_id,
            sha256=sha256,
            mime_type=mimetypes.guess_type(member.filename)[0],
            filename=member.filename,
            original_path=member.source_locator,
            size_bytes=size,
            metadata=_metadata(member, {"external": True}, self.adapter_name),
            authority_kind="unknown",
        )
        relationships: tuple[NormalizedRelationship, ...] = ()
        records: tuple[NormalizedRecord, ...] = ()
        if member.product_key == "notes":
            container = _note_container(member)
            records = (container,)
            rel_digest = hashlib.sha256(
                f"{container.source_record_id}\x00{source_id}".encode()
            ).hexdigest()
            relationships = (
                NormalizedRelationship(
                    source="apple",
                    relationship_type="note_container_has_attachment",
                    source_relationship_namespace=(
                        "apple_privacy/notes/container_attachment/relationship"
                    ),
                    source_relationship_id=rel_digest,
                    subject_kind="record",
                    subject_source="apple",
                    subject_namespace=container.source_record_namespace,
                    subject_source_id=container.source_record_id,
                    object_kind="asset",
                    object_source="apple",
                    object_namespace=asset.asset_namespace,
                    object_source_id=source_id,
                    metadata=_metadata(
                        member, {"provider_evidence": "shared_note_directory"}, self.adapter_name
                    ),
                    authority_kind="unknown",
                ),
            )
        return _export(
            member,
            sha256,
            adapter=self.adapter_name,
            records=records,
            assets=(asset,),
            relationships=relationships,
        )


ADAPTERS: dict[str, AppleAdapter] = {
    adapter.adapter_name: adapter
    for adapter in (
        StructuredAdapter(),
        ContactsAdapter(),
        CalendarAdapter(),
        NotesAdapter(),
        TextAdapter(),
        AssetAdapter(),
    )
}
