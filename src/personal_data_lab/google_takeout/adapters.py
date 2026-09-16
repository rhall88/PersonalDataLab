from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol, runtime_checkable
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from ..fingerprints import FingerprintError, canonical_json_bytes
from ..google_importer import (
    _is_archive_activity_path,
    _parse_activity_stream,
    _path_product,
    _timestamp,
)
from ..models import NormalizedRecord, ParsedExport
from .inventory import GENERIC_STRUCTURED_EXTENSIONS, MAX_GENERIC_MEMBER_BYTES, TakeoutMember
from .products import SupportLevel


class GoogleAdapterError(ValueError):
    """Raised when a matched member cannot be parsed safely by its adapter."""


class AdapterAmbiguityError(GoogleAdapterError):
    """Raised when more than one semantic adapter claims the same member."""


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    reason: str
    requires_byte_level_inspection: bool = False


@runtime_checkable
class GoogleTakeoutAdapter(Protocol):
    adapter_name: str
    parser_version: str
    product_keys: tuple[str, ...]
    supported_patterns: tuple[str, ...]
    support_level: SupportLevel
    representation_kind: str
    is_fallback: bool

    def probe(self, member: TakeoutMember) -> MatchResult: ...

    def parse(self, stream: BinaryIO, member: TakeoutMember) -> ParsedExport: ...


class AdapterRegistry:
    def __init__(self, adapters: tuple[GoogleTakeoutAdapter, ...] = ()) -> None:
        self._adapters: dict[str, GoogleTakeoutAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    @property
    def adapters(self) -> tuple[GoogleTakeoutAdapter, ...]:
        return tuple(self._adapters[name] for name in sorted(self._adapters))

    def register(self, adapter: GoogleTakeoutAdapter) -> None:
        if adapter.adapter_name in self._adapters:
            raise ValueError(f"duplicate Google adapter name: {adapter.adapter_name}")
        self._adapters[adapter.adapter_name] = adapter

    def resolve(self, member: TakeoutMember) -> GoogleTakeoutAdapter | None:
        semantic = tuple(
            adapter
            for adapter in self.adapters
            if not adapter.is_fallback and adapter.probe(member).matched
        )
        if len(semantic) > 1:
            names = ", ".join(adapter.adapter_name for adapter in semantic)
            raise AdapterAmbiguityError(
                f"member {member.member_path} matched multiple adapters: {names}"
            )
        if semantic:
            return semantic[0]
        fallback = tuple(
            adapter
            for adapter in self.adapters
            if adapter.is_fallback and adapter.probe(member).matched
        )
        if len(fallback) > 1:
            names = ", ".join(adapter.adapter_name for adapter in fallback)
            raise AdapterAmbiguityError(
                f"member {member.member_path} matched multiple fallback adapters: {names}"
            )
        return fallback[0] if fallback else None


class MyActivityAdapter:
    adapter_name = "my_activity"
    parser_version = "google-my-activity-v1"
    product_keys = ("my_activity", "youtube")
    supported_patterns = (
        "**/MyActivity.json",
        "**/MyActivity.html",
        "**/watch-history.json",
        "**/search-history.json",
    )
    support_level: SupportLevel = "fully_structured"
    representation_kind = "events"
    is_fallback = False

    def probe(self, member: TakeoutMember) -> MatchResult:
        return MatchResult(
            member.candidate_adapter == self.adapter_name
            and _is_archive_activity_path(member.member_path)
            and not member.is_directory,
            "PR4 My Activity path and filename match",
            requires_byte_level_inspection=True,
        )

    def parse(self, stream: BinaryIO, member: TakeoutMember) -> ParsedExport:
        return _parse_activity_stream(
            stream,
            source_name=member.member_path,
            original_path=member.source_locator,
            size_bytes=member.uncompressed_size,
            exported_at=member.exported_at,
            export_id=member.export_id,
            path_product=_path_product(member.member_path),
        )


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


def _provider_identifier(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("id", "ID", "Id", "uuid", "UUID", "uid", "UID"):
        candidate = value.get(key)
        if isinstance(candidate, (str, int)) and not isinstance(candidate, bool):
            return str(candidate)
    fields = value.get("fields")
    if isinstance(fields, dict):
        return _provider_identifier(fields)
    return None


def _first_string(value: object, keys: tuple[str, ...]) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
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


class GenericStructuredAdapter:
    adapter_name = "generic_structured"
    parser_version = "google-generic-structured-v1"
    product_keys = ()
    supported_patterns = ("**/*.json", "**/*.csv", "**/*.xml")
    support_level: SupportLevel = "partially_structured"
    representation_kind = "records"
    is_fallback = True

    def probe(self, member: TakeoutMember) -> MatchResult:
        matched = (
            member.product_key is not None
            and member.product_key != "my_activity"
            and member.extension in GENERIC_STRUCTURED_EXTENSIONS
            and member.uncompressed_size <= MAX_GENERIC_MEMBER_BYTES
            and not member.is_directory
            and member.accounting_category == "classified"
        )
        return MatchResult(
            matched,
            "known product with a bounded JSON, CSV, or XML member",
            requires_byte_level_inspection=True,
        )

    def _decode(self, content: bytes, member: TakeoutMember) -> list[object]:
        text = content.decode("utf-8-sig", errors="strict")
        if not text.strip():
            return []
        if member.extension == ".json":
            value = json.loads(text, parse_constant=_json_constant)
            return list(value) if isinstance(value, list) else [value]
        if member.extension == ".csv":
            rows = list(csv.reader(io.StringIO(text, newline="")))
            if not rows:
                return []
            headers = rows[0]
            unique_headers = len(headers) == len(set(headers))
            decoded: list[object] = []
            for values in rows[1:]:
                item: dict[str, object] = {"headers": headers, "values": values}
                if unique_headers:
                    item["fields"] = {
                        header: values[index] if index < len(values) else None
                        for index, header in enumerate(headers)
                    }
                decoded.append(item)
            return decoded
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise ValueError("XML declarations with entities are not supported")
        root = ElementTree.fromstring(text)
        return [{"xml": _xml_value(root), "raw_xml": text}]

    def parse(self, stream: BinaryIO, member: TakeoutMember) -> ParsedExport:
        if member.uncompressed_size > MAX_GENERIC_MEMBER_BYTES:
            raise GoogleAdapterError(
                f"generic structured member exceeds bounded parser limit: {member.member_path}"
            )
        content = stream.read(MAX_GENERIC_MEMBER_BYTES + 1)
        if len(content) > MAX_GENERIC_MEMBER_BYTES:
            raise GoogleAdapterError(
                f"generic structured member exceeds bounded parser limit: {member.member_path}"
            )
        if len(content) != member.uncompressed_size:
            raise GoogleAdapterError(
                f"member size changed while reading: {member.member_path}"
            )
        try:
            values = self._decode(content, member)
            product_key = member.product_key
            if product_key is None:
                raise GoogleAdapterError("generic adapter requires a known Google product")
            representation = member.extension.removeprefix(".")
            namespace = f"google_takeout/{product_key}/{representation}"
            identities: dict[str, int] = {}
            records: list[NormalizedRecord] = []
            warnings: list[str] = []
            for index, value in enumerate(values):
                provider_id = _provider_identifier(value)
                if provider_id is None:
                    identity = "content:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()
                else:
                    identity = "provider:" + provider_id
                identities[identity] = identities.get(identity, 0) + 1
                if identities[identity] > 1:
                    identity = f"{identity}:{identities[identity]}"
                created_raw = _first_string(value, ("created_at", "createdAt", "created"))
                updated_raw = _first_string(value, ("updated_at", "updatedAt", "updated"))
                occurred_raw = _first_string(value, ("occurred_at", "occurredAt", "time", "date"))
                updated_at = _timestamp(updated_raw)
                created_at = _timestamp(created_raw)
                occurred_at = _timestamp(occurred_raw)
                for field_name, raw_value, normalized_value in (
                    ("created_at", created_raw, created_at),
                    ("updated_at", updated_raw, updated_at),
                    ("occurred_at", occurred_raw, occurred_at),
                ):
                    if raw_value is not None and normalized_value is None:
                        warnings.append(
                            f"record {index} {field_name} could not be normalized"
                        )
                projection_source = (
                    value.get("xml")
                    if representation == "xml" and isinstance(value, dict)
                    else value
                )
                records.append(
                    NormalizedRecord(
                        source="google",
                        record_type="generic_google_record",
                        source_record_namespace=namespace,
                        source_record_id=identity,
                        title=_first_string(value, ("title", "name", "subject")),
                        text=_text_projection(projection_source),
                        created_at=created_at,
                        updated_at=updated_at,
                        occurred_at=occurred_at,
                        actor_identity="account_owner",
                        metadata={
                            "google": value,
                            "_pdl": {
                                "takeout_product": product_key,
                                "representation": representation,
                                "generic_adapter": True,
                            },
                        },
                        provider_updated_at=updated_at,
                        authority_at=updated_at or member.exported_at,
                        authority_kind=(
                            "provider_updated_at"
                            if updated_at is not None
                            else "exported_at"
                            if member.exported_at is not None
                            else "unknown"
                        ),
                    )
                )
        except (csv.Error, ElementTree.ParseError, FingerprintError, UnicodeDecodeError, ValueError) as error:
            if isinstance(error, GoogleAdapterError):
                raise
            raise GoogleAdapterError(
                f"could not parse generic structured member {member.member_path}"
            ) from error

        return ParsedExport(
            source="google",
            parser_version=self.parser_version,
            category=f"takeout/{product_key}/generic/{representation}",
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
        )


@contextmanager
def open_takeout_member(member: TakeoutMember) -> Iterator[BinaryIO]:
    """Open one exact member as a bounded stream without materializing an archive."""

    if member.is_directory or member.accounting_category == "unknown":
        raise GoogleAdapterError(f"member is not safe to open: {member.member_path}")
    if member.container_kind == "zip":
        try:
            with ZipFile(member.archive_path) as archive, archive.open(
                member.member_path
            ) as stream:
                yield stream
        except (BadZipFile, KeyError, OSError, RuntimeError) as error:
            raise GoogleAdapterError(
                f"could not open Takeout member {member.member_path}"
            ) from error
        return
    relative = PurePosixPath(member.member_path)
    candidate = Path(member.archive_path).joinpath(*relative.parts).resolve(strict=True)
    container = Path(member.archive_path).resolve(strict=True)
    if container != candidate and container not in candidate.parents:
        raise GoogleAdapterError(f"extracted member escapes its container: {member.member_path}")
    with candidate.open("rb") as stream:
        yield stream


# Semantic adapters are imported after the protocol and stream helpers are defined to avoid a
# circular dependency while keeping the public registry in one place.
from .semantic import PR6_ADAPTERS

DEFAULT_ADAPTER_REGISTRY = AdapterRegistry(
    (MyActivityAdapter(), *PR6_ADAPTERS, GenericStructuredAdapter())
)
