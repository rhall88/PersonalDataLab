from __future__ import annotations

import csv
import hashlib
import io
import json
import mimetypes
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from ..google_importer import _timestamp
from ..models import NormalizedAsset, NormalizedRecord, NormalizedRelationship, ParsedExport
from .adapters import GoogleAdapterError, MatchResult
from .inventory import TakeoutMember
from .products import SupportLevel
from .semantic import (
    SEMANTIC_MAX_MEMBER_BYTES,
    _authority,
    _decode_structured,
    _dedupe_identity,
    _first_string,
    _identity,
    _parsed_export,
    _read_bounded,
    _text_projection,
)

RICH_ASSET_PRODUCTS = ("google_photos", "drive", "recorder", "phone_audio", "street_view")
TEXT_EXTENSIONS = (
    ".json",
    ".csv",
    ".xml",
    ".html",
    ".htm",
    ".txt",
    ".md",
    ".ics",
    ".vcf",
    ".sh",
    ".tasks",
)
OFFICE_EXTENSIONS = (".docx", ".pptx", ".xlsx")
MAX_TEXT_PROJECTION_BYTES = 2 * 1024 * 1024


def _path_identity(path: str) -> str:
    normalized = PurePosixPath(path).as_posix()
    return "path:" + hashlib.sha256(normalized.encode()).hexdigest()


def _member_relative_path(member: TakeoutMember) -> str:
    return member.product_relative_path or member.member_path


def _asset(
    member: TakeoutMember,
    *,
    sha256: str,
    size_bytes: int,
    metadata: dict[str, object] | None = None,
) -> NormalizedAsset:
    authority_at, authority_kind = _authority(member, None)
    return NormalizedAsset(
        source="google",
        asset_namespace=f"google_takeout/{member.product_key}/asset",
        source_asset_id=_path_identity(_member_relative_path(member)),
        sha256=sha256,
        mime_type=mimetypes.guess_type(member.filename)[0],
        filename=member.filename,
        original_path=member.source_locator,
        size_bytes=size_bytes,
        metadata={
            "_pdl": {
                "takeout_product": member.product_key,
                "external_content": True,
                **(metadata or {}),
            }
        },
        authority_at=authority_at,
        authority_kind=authority_kind,
    )


def _relationship(
    *,
    product_key: str,
    relationship_type: str,
    subject_namespace: str,
    subject_id: str,
    asset_id: str,
    authority_at: object,
    authority_kind: str,
    metadata: dict[str, object] | None = None,
) -> NormalizedRelationship:
    return NormalizedRelationship(
        source="google",
        relationship_type=relationship_type,
        source_relationship_namespace=f"google_takeout/{product_key}/{relationship_type}",
        source_relationship_id=hashlib.sha256(
            f"{subject_namespace}\0{subject_id}\0{asset_id}".encode()
        ).hexdigest(),
        subject_kind="record",
        subject_source="google",
        subject_namespace=subject_namespace,
        subject_source_id=subject_id,
        object_kind="asset",
        object_source="google",
        object_namespace=f"google_takeout/{product_key}/asset",
        object_source_id=asset_id,
        metadata={"_pdl": {"provider_evidenced": True, **(metadata or {})}},
        authority_at=authority_at,
        authority_kind=authority_kind,
    )


def _nested_time(value: object, *paths: tuple[str, ...]):
    for path in paths:
        candidate = value
        for component in path:
            if not isinstance(candidate, dict):
                candidate = None
                break
            candidate = candidate.get(component)
        if isinstance(candidate, (str, int, float)):
            parsed = _timestamp(candidate)
            if parsed is not None:
                return parsed
    return None


def _photo_target_path(member: TakeoutMember, value: dict[str, object]) -> str | None:
    title = _first_string(value, ("title", "filename"))
    if not title:
        return None
    filename = PurePosixPath(title).name
    relative = PurePosixPath(_member_relative_path(member))
    return (relative.parent / filename).as_posix()


def _photo_sidecar(content: bytes, member: TakeoutMember) -> ParsedExport:
    decoded = json.loads(content.decode("utf-8-sig"))
    values = decoded if isinstance(decoded, list) else [decoded]
    records: list[NormalizedRecord] = []
    relationships: list[NormalizedRelationship] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(values):
        value = raw if isinstance(raw, dict) else {"value": raw}
        target_path = _photo_target_path(member, value)
        is_media = bool(
            target_path
            and any(key in value for key in ("photoTakenTime", "creationTime", "geoData", "url"))
        )
        captured_at = _nested_time(
            value,
            ("photoTakenTime", "timestamp"),
            ("creationTime", "timestamp"),
        )
        authority_at, authority_kind = _authority(member, captured_at)
        if is_media and target_path is not None:
            target_extension = PurePosixPath(target_path).suffix.casefold()
            record_type = (
                "video_metadata"
                if target_extension in {".3gp", ".avi", ".mkv", ".mov", ".mp4", ".webm"}
                else "photo_metadata"
            )
            source_id = _dedupe_identity(_path_identity(target_path), seen)
            namespace = "google_takeout/google_photos/media_metadata/json"
            records.append(
                NormalizedRecord(
                    source="google",
                    record_type=record_type,
                    source_record_namespace=namespace,
                    source_record_id=source_id,
                    title=_first_string(value, ("title",)),
                    text=_first_string(value, ("description",)),
                    created_at=_nested_time(value, ("creationTime", "timestamp")),
                    occurred_at=captured_at,
                    actor_identity="account_owner",
                    metadata={
                        "google": value,
                        "_pdl": {
                            "semantic_adapter": "photos_semantic",
                            "expected_asset_path": target_path,
                        },
                    },
                    provider_updated_at=captured_at,
                    authority_at=authority_at,
                    authority_kind=authority_kind,
                )
            )
            relationships.append(
                _relationship(
                    product_key="google_photos",
                    relationship_type="metadata_describes_asset",
                    subject_namespace=namespace,
                    subject_id=source_id,
                    asset_id=_path_identity(target_path),
                    authority_at=authority_at,
                    authority_kind=authority_kind,
                )
            )
            continue

        namespace = "google_takeout/google_photos/album/json"
        album_id = _dedupe_identity(_identity(value, prefix=str(index)), seen)
        records.append(
            NormalizedRecord(
                source="google",
                record_type="photo_album",
                source_record_namespace=namespace,
                source_record_id=album_id,
                title=_first_string(value, ("title", "name")),
                text=_text_projection(value),
                occurred_at=_nested_time(value, ("date", "timestamp"), ("creationTime", "timestamp")),
                actor_identity="account_owner",
                metadata={
                    "google": value,
                    "_pdl": {"semantic_adapter": "photos_semantic"},
                },
                authority_at=authority_at,
                authority_kind=authority_kind,
            )
        )
        entries = value.get("entries")
        if isinstance(entries, list):
            relative = PurePosixPath(_member_relative_path(member))
            for entry_index, entry in enumerate(entries):
                filename = _first_string(entry, ("text", "title", "filename"))
                if not filename:
                    continue
                target = (relative.parent / PurePosixPath(filename).name).as_posix()
                relationships.append(
                    _relationship(
                        product_key="google_photos",
                        relationship_type="album_contains_asset",
                        subject_namespace=namespace,
                        subject_id=album_id,
                        asset_id=_path_identity(target),
                        authority_at=authority_at,
                        authority_kind=authority_kind,
                        metadata={"source_order": entry_index},
                    )
                )
    return _parsed_export(
        member=member,
        content=content,
        parser_version="google-photos-v1",
        category="takeout/google_photos/semantic/json",
        records=records,
        relationships=relationships,
    )


def _bounded_text(value: str, warnings: list[str]) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= MAX_TEXT_PROJECTION_BYTES:
        return value
    warnings.append("document text exceeded normalized projection limit")
    return encoded[:MAX_TEXT_PROJECTION_BYTES].decode("utf-8", errors="ignore")


def _office_text(content: bytes, extension: str) -> str:
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            if extension == ".docx":
                names = ["word/document.xml"]
            elif extension == ".pptx":
                names = sorted(
                    name
                    for name in archive.namelist()
                    if name.startswith("ppt/slides/slide") and name.endswith(".xml")
                )
            else:
                names = sorted(
                    name
                    for name in archive.namelist()
                    if name == "xl/sharedStrings.xml"
                    or (name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
                )
            parts: list[str] = []
            for name in names:
                source = archive.read(name)
                if b"<!DOCTYPE" in source.upper() or b"<!ENTITY" in source.upper():
                    raise GoogleAdapterError("Office XML declarations with entities are not supported")
                root = ElementTree.fromstring(source)
                parts.extend(text.strip() for text in root.itertext() if text.strip())
            return "\n".join(parts)
    except (BadZipFile, KeyError, ElementTree.ParseError, OSError) as error:
        if isinstance(error, GoogleAdapterError):
            raise
        raise GoogleAdapterError("could not parse Office Open XML document") from error


def _structured_values(content: bytes, extension: str) -> list[object]:
    if extension in {".json", ".xml", ".html", ".htm", ".txt"}:
        return _decode_structured(content, extension)
    text = content.decode("utf-8-sig", errors="strict")
    if not text.strip():
        return []
    if extension == ".csv":
        sample = text[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel
        return [dict(row) for row in csv.DictReader(io.StringIO(text, newline=""), dialect=dialect)]
    return [{"raw_text": text}]


def _document_export(content: bytes, member: TakeoutMember) -> ParsedExport:
    product_key = member.product_key or "drive"
    content_sha256 = hashlib.sha256(content).hexdigest()
    asset = _asset(member, sha256=content_sha256, size_bytes=len(content))
    warnings: list[str] = []
    if member.extension in OFFICE_EXTENSIONS:
        values: list[object] = [{"extracted_text": _office_text(content, member.extension)}]
    else:
        values = _structured_values(content, member.extension)
    records: list[NormalizedRecord] = []
    relationships: list[NormalizedRelationship] = []
    seen: dict[str, int] = {}
    authority_at, authority_kind = _authority(member, None)
    namespace = f"google_takeout/{product_key}/document/{member.extension.removeprefix('.') or 'none'}"
    for index, raw in enumerate(values):
        value = raw if isinstance(raw, dict) else {"value": raw}
        source_id = _dedupe_identity(
            _identity(value, prefix=f"{_path_identity(_member_relative_path(member))}:{index}"),
            seen,
        )
        text = _bounded_text(_text_projection(value) or "", warnings) or None
        records.append(
            NormalizedRecord(
                source="google",
                record_type=("transcript" if product_key == "recorder" else "document"),
                source_record_namespace=namespace,
                source_record_id=source_id,
                title=member.filename,
                text=text,
                actor_identity="account_owner",
                metadata={
                    "google": value,
                    "_pdl": {
                        "semantic_adapter": "rich_document_semantic",
                        "source_path": _member_relative_path(member),
                    },
                },
                authority_at=authority_at,
                authority_kind=authority_kind,
            )
        )
        target_asset_id = asset.source_asset_id
        relationship_type = "document_has_source_asset"
        if product_key == "recorder" and member.extension == ".txt":
            relative = PurePosixPath(_member_relative_path(member)).with_suffix(".m4a").as_posix()
            target_asset_id = _path_identity(relative)
            relationship_type = "transcript_describes_audio"
        relationships.append(
            _relationship(
                product_key=product_key,
                relationship_type=relationship_type,
                subject_namespace=namespace,
                subject_id=source_id,
                asset_id=target_asset_id,
                authority_at=authority_at,
                authority_kind=authority_kind,
                metadata={"source_order": index},
            )
        )
    return _parsed_export(
        member=member,
        content=content,
        parser_version="google-rich-document-v1",
        category=f"takeout/{product_key}/semantic/document/{member.extension.removeprefix('.')}",
        records=records,
        assets=(asset,),
        relationships=relationships,
        warnings=warnings,
    )


@dataclass(frozen=True)
class RichStructuredAdapter:
    adapter_name: str = "rich_asset_semantic"
    parser_version: str = "google-rich-assets-v1"
    product_keys: tuple[str, ...] = RICH_ASSET_PRODUCTS
    supported_patterns: tuple[str, ...] = tuple(f"**/*{extension}" for extension in (*TEXT_EXTENSIONS, *OFFICE_EXTENSIONS))
    support_level: SupportLevel = "fully_structured"
    representation_kind: str = "records+assets+relationships"
    is_fallback: bool = False

    def probe(self, member: TakeoutMember) -> MatchResult:
        return MatchResult(
            member.product_key in self.product_keys
            and member.extension in {*TEXT_EXTENSIONS, *OFFICE_EXTENSIONS}
            and member.candidate_adapter != "my_activity"
            and member.uncompressed_size <= SEMANTIC_MAX_MEMBER_BYTES
            and not member.is_directory
            and member.accounting_category == "classified",
            "bounded rich-asset metadata/document representation",
            requires_byte_level_inspection=True,
        )

    def parse(self, stream: BinaryIO, member: TakeoutMember) -> ParsedExport:
        content = _read_bounded(stream, member)
        try:
            if member.product_key == "google_photos" and member.extension == ".json":
                return _photo_sidecar(content, member)
            return _document_export(content, member)
        except (UnicodeDecodeError, json.JSONDecodeError, csv.Error, ValueError) as error:
            if isinstance(error, GoogleAdapterError):
                raise
            raise GoogleAdapterError(
                f"could not parse rich-asset member {member.member_path}"
            ) from error


@dataclass(frozen=True)
class RichBinaryAssetAdapter:
    adapter_name: str = "rich_binary_asset"
    parser_version: str = "google-rich-binary-asset-v1"
    product_keys: tuple[str, ...] = RICH_ASSET_PRODUCTS
    supported_patterns: tuple[str, ...] = ("**/*",)
    support_level: SupportLevel = "fully_structured"
    representation_kind: str = "assets"
    is_fallback: bool = False

    def probe(self, member: TakeoutMember) -> MatchResult:
        structured = (
            member.candidate_adapter == "my_activity"
            or (
                member.extension in {*TEXT_EXTENSIONS, *OFFICE_EXTENSIONS}
                and member.uncompressed_size <= SEMANTIC_MAX_MEMBER_BYTES
            )
        )
        return MatchResult(
            member.product_key in self.product_keys
            and not structured
            and not member.is_directory
            and member.accounting_category == "classified",
            "streamed external rich asset representation",
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
        parsed = _parsed_export(
            member=member,
            content=b"",
            parser_version=self.parser_version,
            category=f"takeout/{member.product_key}/semantic/asset",
            assets=(_asset(member, sha256=content_sha256, size_bytes=size),),
        )
        return parsed.model_copy(update={"sha256": content_sha256, "size_bytes": size})


PR8_ADAPTERS = (RichStructuredAdapter(), RichBinaryAssetAdapter())
