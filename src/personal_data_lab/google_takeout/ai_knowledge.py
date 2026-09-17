from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import BinaryIO

from ..models import NormalizedRecord, NormalizedRelationship, ParsedExport
from .adapters import GoogleAdapterError, MatchResult
from .inventory import TakeoutMember
from .products import SupportLevel
from .rich_assets import _asset, _office_text, _path_identity
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
    _time,
)

AI_KNOWLEDGE_PRODUCTS = (
    "gemini",
    "gemini_workspace",
    "notebooklm",
    "flow",
    "workspace_studio",
    "keep",
    "journal",
    "pinpoint",
    "cloud_search",
)
STRUCTURED_EXTENSIONS = (".json", ".csv", ".html", ".htm", ".txt", ".md", ".pptx")


def _relative(member: TakeoutMember) -> str:
    return member.product_relative_path or member.member_path


def _notebook_identity(member: TakeoutMember) -> str:
    parts = PurePosixPath(_relative(member)).parts
    root = parts[0] if len(parts) > 1 else "."
    return _path_identity(root)


def _is_notebook_child_document(member: TakeoutMember) -> bool:
    if member.product_key != "notebooklm":
        return False
    parts = PurePosixPath(_relative(member)).parts
    return len(parts) >= 3 and parts[1].casefold() in {
        "artifacts",
        "discovered sources",
        "sources",
    }


def _unix_time(seconds: object, nanos: object = 0) -> datetime | None:
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return None
    fraction = nanos / 1_000_000_000 if isinstance(nanos, (int, float)) else 0
    try:
        return datetime.fromtimestamp(float(seconds) + float(fraction), tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _usec_time(value: object) -> datetime | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1_000_000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _record(
    *,
    member: TakeoutMember,
    value: dict[str, object],
    record_type: str,
    namespace: str,
    source_id: str,
    title: str | None = None,
    text: str | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    occurred_at: datetime | None = None,
) -> NormalizedRecord:
    authority_at, authority_kind = _authority(member, updated_at or occurred_at or created_at)
    return NormalizedRecord(
        source="google",
        record_type=record_type,
        source_record_namespace=namespace,
        source_record_id=source_id,
        title=title,
        text=text,
        created_at=created_at,
        updated_at=updated_at,
        occurred_at=occurred_at,
        actor_identity="account_owner",
        metadata={
            "google": value,
            "_pdl": {
                "semantic_adapter": "ai_knowledge_semantic",
                "source_path": _relative(member),
            },
        },
        provider_updated_at=updated_at,
        authority_at=authority_at,
        authority_kind=authority_kind,
    )


def _flow_json(value: object, member: TakeoutMember) -> tuple[list[NormalizedRecord], list[NormalizedRelationship]]:
    root = value if isinstance(value, dict) else {"value": value}
    records: list[NormalizedRecord] = []
    relationships: list[NormalizedRelationship] = []
    seen: dict[str, int] = {}
    projects = root.get("projects")
    if isinstance(projects, list):
        for index, raw in enumerate(projects):
            project = raw if isinstance(raw, dict) else {"value": raw}
            project_id = _dedupe_identity(
                (
                    f"provider:{project['projectId']}"
                    if isinstance(project.get("projectId"), str) and project["projectId"]
                    else _identity(project, prefix=str(index))
                ),
                seen,
            )
            records.append(
                _record(
                    member=member,
                    value=project,
                    record_type="ai_project",
                    namespace="google_takeout/flow/project/json",
                    source_id=project_id,
                    title=_first_string(project, ("name", "title")),
                    text=_text_projection(project),
                    created_at=_time(project, ("createdAt", "createTime")),
                )
            )
    media_entries = root.get("mediaEntries")
    if isinstance(media_entries, list):
        for index, raw in enumerate(media_entries):
            media = raw if isinstance(raw, dict) else {"value": raw}
            media_id = _dedupe_identity(
                (
                    f"provider:{media['mediaId']}"
                    if isinstance(media.get("mediaId"), str) and media["mediaId"]
                    else _identity(media, prefix=str(index))
                ),
                seen,
            )
            namespace = "google_takeout/flow/generated_media/json"
            records.append(
                _record(
                    member=member,
                    value=media,
                    record_type="generated_media",
                    namespace=namespace,
                    source_id=media_id,
                    title=_first_string(media, ("mediaTitle", "title")),
                    text=_text_projection(media.get("generationInputs")),
                    created_at=_time(media, ("createdAt", "createTime")),
                )
            )
            project_id = media.get("projectId")
            if isinstance(project_id, str) and project_id:
                relationship_id = hashlib.sha256(
                    f"{project_id}\0{media_id}".encode()
                ).hexdigest()
                authority_at, authority_kind = _authority(
                    member, _time(media, ("createdAt", "createTime"))
                )
                relationships.append(
                    NormalizedRelationship(
                        source="google",
                        relationship_type="project_contains_generated_media",
                        source_relationship_namespace="google_takeout/flow/project_media",
                        source_relationship_id=relationship_id,
                        subject_kind="record",
                        subject_source="google",
                        subject_namespace="google_takeout/flow/project/json",
                        subject_source_id=f"provider:{project_id}",
                        object_kind="record",
                        object_source="google",
                        object_namespace=namespace,
                        object_source_id=media_id,
                        metadata={"_pdl": {"provider_evidenced": True}},
                        authority_at=authority_at,
                        authority_kind=authority_kind,
                    )
                )
    return records, relationships


def _json_records(content: bytes, member: TakeoutMember) -> tuple[list[NormalizedRecord], list[NormalizedRelationship]]:
    decoded = json.loads(content.decode("utf-8-sig")) if content.strip() else []
    product_key = member.product_key
    if product_key == "flow":
        return _flow_json(decoded, member)
    values = decoded if isinstance(decoded, list) else [decoded]
    records: list[NormalizedRecord] = []
    relationships: list[NormalizedRelationship] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(values):
        value = raw if isinstance(raw, dict) else {"value": raw}
        if not value:
            continue
        if product_key == "keep":
            created_at = _usec_time(value.get("createdTimestampUsec"))
            updated_at = _usec_time(value.get("userEditedTimestampUsec"))
            records.append(
                _record(
                    member=member,
                    value=value,
                    record_type="note",
                    namespace="google_takeout/keep/note/json",
                    source_id=_dedupe_identity(
                        _path_identity(PurePosixPath(_relative(member)).with_suffix("").as_posix()),
                        seen,
                    ),
                    title=_first_string(value, ("title",)),
                    text=_first_string(value, ("textContent", "textContentHtml")),
                    created_at=created_at,
                    updated_at=updated_at,
                )
            )
            continue
        if product_key == "journal":
            wrapper = value.get("journal_content")
            entity = wrapper.get("journalContentEntity") if isinstance(wrapper, dict) else value
            entity = entity if isinstance(entity, dict) else value
            created_raw = entity.get("createdTime")
            modified_raw = entity.get("modifiedTime")
            created_at = _unix_time(
                created_raw.get("seconds") if isinstance(created_raw, dict) else None,
                created_raw.get("nanos") if isinstance(created_raw, dict) else 0,
            )
            updated_at = _unix_time(
                modified_raw.get("seconds") if isinstance(modified_raw, dict) else None,
                modified_raw.get("nanos") if isinstance(modified_raw, dict) else 0,
            )
            records.append(
                _record(
                    member=member,
                    value=value,
                    record_type="note",
                    namespace="google_takeout/journal/note/json",
                    source_id=_dedupe_identity(_identity(entity, prefix=str(index)), seen),
                    title=_first_string(entity, ("title",)),
                    text=_first_string(entity, ("content", "text")),
                    created_at=created_at,
                    updated_at=updated_at,
                )
            )
            continue
        if product_key == "notebooklm":
            metadata = value.get("metadata")
            metadata_value = metadata if isinstance(metadata, dict) else {}
            records.append(
                _record(
                    member=member,
                    value=value,
                    record_type="notebook",
                    namespace="google_takeout/notebooklm/notebook/json",
                    source_id=_notebook_identity(member),
                    title=_first_string(value, ("title", "name")),
                    text=_text_projection(value),
                    created_at=_time(metadata_value, ("createTime", "createdAt")),
                    occurred_at=_time(metadata_value, ("lastViewed",)),
                )
            )
            continue
        records.append(
            _record(
                member=member,
                value=value,
                record_type=("ai_project" if product_key == "workspace_studio" else "document"),
                namespace=f"google_takeout/{product_key}/record/json",
                source_id=_dedupe_identity(_identity(value, prefix=str(index)), seen),
                title=_first_string(value, ("title", "name")),
                text=_text_projection(value),
                created_at=_time(value, ("createdAt", "createTime")),
                updated_at=_time(value, ("updatedAt", "modifiedTime")),
            )
        )
    return records, relationships


def _document_records(
    content: bytes, member: TakeoutMember
) -> tuple[list[NormalizedRecord], list[NormalizedRelationship]]:
    product_key = member.product_key or "knowledge"
    if member.extension == ".pptx":
        values: list[object] = [{"extracted_text": _office_text(content, ".pptx")}]
    else:
        values = _decode_structured(
            content,
            ".txt" if member.extension == ".md" else member.extension,
        )
    record_type = (
        "conversation"
        if product_key in {"gemini", "gemini_workspace"}
        else "note"
        if product_key == "keep"
        else "document"
    )
    namespace = f"google_takeout/{product_key}/{record_type}/{member.extension.removeprefix('.') or 'none'}"
    path_id = _path_identity(PurePosixPath(_relative(member)).with_suffix("").as_posix())
    records: list[NormalizedRecord] = []
    relationships: list[NormalizedRelationship] = []
    for index, raw in enumerate(values):
        value = raw if isinstance(raw, dict) else {"value": raw}
        visible_text = (
            value.get("visible_text")
            or value.get("raw_text")
            or value.get("extracted_text")
        )
        text = str(visible_text) if visible_text is not None else _text_projection(value)
        source_id = path_id if len(values) == 1 else f"{path_id}:{index}"
        records.append(
            _record(
                member=member,
                value=value,
                record_type=record_type,
                namespace=namespace,
                source_id=source_id,
                title=member.filename,
                text=text,
            )
        )
        if product_key == "notebooklm":
            authority_at, authority_kind = _authority(member, None)
            notebook_id = _notebook_identity(member)
            relationships.append(
                NormalizedRelationship(
                    source="google",
                    relationship_type="notebook_contains_document",
                    source_relationship_namespace="google_takeout/notebooklm/notebook_document",
                    source_relationship_id=hashlib.sha256(
                        f"{notebook_id}\0{namespace}\0{source_id}".encode()
                    ).hexdigest(),
                    subject_kind="record",
                    subject_source="google",
                    subject_namespace="google_takeout/notebooklm/notebook/json",
                    subject_source_id=notebook_id,
                    object_kind="record",
                    object_source="google",
                    object_namespace=namespace,
                    object_source_id=source_id,
                    metadata={"_pdl": {"provider_evidenced": True}},
                    authority_at=authority_at,
                    authority_kind=authority_kind,
                )
            )
    return records, relationships


@dataclass(frozen=True)
class AIKnowledgeStructuredAdapter:
    adapter_name: str = "ai_knowledge_semantic"
    parser_version: str = "google-ai-knowledge-v2"
    product_keys: tuple[str, ...] = AI_KNOWLEDGE_PRODUCTS
    supported_patterns: tuple[str, ...] = tuple(
        f"**/*{extension}" for extension in STRUCTURED_EXTENSIONS
    )
    support_level: SupportLevel = "fully_structured"
    representation_kind: str = "records+relationships"
    is_fallback: bool = False

    def probe(self, member: TakeoutMember) -> MatchResult:
        return MatchResult(
            member.product_key in self.product_keys
            and member.extension in STRUCTURED_EXTENSIONS
            and member.uncompressed_size <= SEMANTIC_MAX_MEMBER_BYTES
            and not member.is_directory
            and member.accounting_category == "classified",
            "bounded AI/knowledge/notes semantic representation",
            requires_byte_level_inspection=True,
        )

    def parse(self, stream: BinaryIO, member: TakeoutMember) -> ParsedExport:
        content = _read_bounded(stream, member)
        try:
            if member.extension == ".json" and not _is_notebook_child_document(member):
                records, relationships = _json_records(content, member)
            else:
                records, relationships = _document_records(content, member)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            if isinstance(error, GoogleAdapterError):
                raise
            raise GoogleAdapterError(
                f"could not parse AI/knowledge member {member.member_path}"
            ) from error
        return _parsed_export(
            member=member,
            content=content,
            parser_version=self.parser_version,
            category=(
                f"takeout/{member.product_key}/semantic/{member.extension.removeprefix('.') or 'none'}"
            ),
            records=records,
            relationships=relationships,
        )


@dataclass(frozen=True)
class AIKnowledgeAssetAdapter:
    adapter_name: str = "ai_knowledge_asset"
    parser_version: str = "google-ai-knowledge-asset-v1"
    product_keys: tuple[str, ...] = AI_KNOWLEDGE_PRODUCTS
    supported_patterns: tuple[str, ...] = ("**/*",)
    support_level: SupportLevel = "fully_structured"
    representation_kind: str = "assets+relationships"
    is_fallback: bool = False

    def probe(self, member: TakeoutMember) -> MatchResult:
        structured = (
            member.extension in STRUCTURED_EXTENSIONS
            and member.uncompressed_size <= SEMANTIC_MAX_MEMBER_BYTES
        )
        return MatchResult(
            member.product_key in self.product_keys
            and not structured
            and not member.is_directory
            and member.accounting_category == "classified",
            "streamed external AI/knowledge asset representation",
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
        asset = _asset(member, sha256=content_sha256, size_bytes=size)
        relationships: list[NormalizedRelationship] = []
        if member.product_key == "notebooklm":
            authority_at, authority_kind = _authority(member, None)
            notebook_id = _notebook_identity(member)
            relationships.append(
                NormalizedRelationship(
                    source="google",
                    relationship_type="notebook_contains_asset",
                    source_relationship_namespace="google_takeout/notebooklm/notebook_asset",
                    source_relationship_id=hashlib.sha256(
                        f"{notebook_id}\0{asset.source_asset_id}".encode()
                    ).hexdigest(),
                    subject_kind="record",
                    subject_source="google",
                    subject_namespace="google_takeout/notebooklm/notebook/json",
                    subject_source_id=notebook_id,
                    object_kind="asset",
                    object_source="google",
                    object_namespace=asset.asset_namespace,
                    object_source_id=asset.source_asset_id,
                    metadata={"_pdl": {"provider_evidenced": True}},
                    authority_at=authority_at,
                    authority_kind=authority_kind,
                )
            )
        parsed = _parsed_export(
            member=member,
            content=b"",
            parser_version=self.parser_version,
            category=f"takeout/{member.product_key}/semantic/asset",
            assets=(asset,),
            relationships=relationships,
        )
        return parsed.model_copy(update={"sha256": content_sha256, "size_bytes": size})


PR9_ADAPTERS = (AIKnowledgeStructuredAdapter(), AIKnowledgeAssetAdapter())
