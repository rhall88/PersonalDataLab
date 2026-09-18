from __future__ import annotations

import hashlib
import json
import mimetypes
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from pydantic import ValidationError

from ..fingerprints import canonical_json_bytes
from ..models import (
    NormalizedAsset,
    NormalizedConversation,
    NormalizedEvent,
    NormalizedRecord,
    NormalizedRelationship,
    ParsedExport,
)
from .inventory import OpenAIExportReader, OpenAIPhysicalMember

SOURCE = "openai"
PARSER_VERSION = "openai-production-v2"
MESSAGE_NAMESPACE = "chatgpt/mapping_message"
CONVERSATION_NAMESPACE = "chatgpt/conversation"
ASSET_NAMESPACE = "chatgpt/export_asset"
LIBRARY_NAMESPACE = "chatgpt/library_file"
CONVERSATIONS_PER_CHUNK = 25


class OpenAIAdapterError(ValueError):
    """Raised when an accounted OpenAI member cannot be normalized safely."""


@dataclass(frozen=True)
class AssetCatalog:
    available_ids: frozenset[str]
    member_paths: dict[str, str]
    original_names: dict[str, str]
    library_records: dict[str, dict[str, Any]]


def _postgres_safe_text(value: str) -> str:
    """Represent NUL losslessly because PostgreSQL text/jsonb cannot store U+0000."""

    return value.replace("\x00", "\\u0000")


def _postgres_safe_json(value: Any) -> Any:
    if isinstance(value, str):
        return _postgres_safe_text(value)
    if isinstance(value, list):
        return [_postgres_safe_json(item) for item in value]
    if isinstance(value, dict):
        return {
            _postgres_safe_text(str(key)): _postgres_safe_json(item) for key, item in value.items()
        }
    return value


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_member(reader: OpenAIExportReader, member_path: str) -> str:
    digest = hashlib.sha256()
    with reader.open_binary(member_path) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith("Z"):
            candidate = f"{candidate[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            try:
                return datetime.fromtimestamp(float(candidate), tz=UTC)
            except (OverflowError, ValueError):
                return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return None


def _stable_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _relationship(
    relationship_type: str,
    subject_kind: str,
    subject_namespace: str,
    subject_id: str,
    object_kind: str,
    object_namespace: str,
    object_id: str,
    *,
    authority_at: datetime | None,
    metadata: dict[str, Any],
) -> NormalizedRelationship:
    identity = _stable_digest(
        {
            "type": relationship_type,
            "subject": [subject_kind, subject_namespace, subject_id],
            "object": [object_kind, object_namespace, object_id],
        }
    )
    return NormalizedRelationship(
        source=SOURCE,
        relationship_type=relationship_type,
        source_relationship_namespace=f"chatgpt/{relationship_type}",
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
        authority_kind="provider_updated_at" if authority_at is not None else "unknown",
    )


def _load_json(reader: OpenAIExportReader, member_path: str) -> tuple[Any, bytes]:
    raw = reader.read_bytes(member_path)
    try:
        return json.loads(raw), raw
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenAIAdapterError("accounted JSON member is invalid") from exc


def load_asset_catalog(
    reader: OpenAIExportReader,
    members: tuple[OpenAIPhysicalMember, ...],
) -> AssetCatalog:
    by_name = {item.member_path: item for item in members}
    names_payload, _ = _load_json(reader, "conversation_asset_file_names.json")
    library_payload, _ = _load_json(reader, "library_files.json")
    if not isinstance(names_payload, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in names_payload.items()
    ):
        raise OpenAIAdapterError("conversation asset name map has an unsupported schema")
    if not isinstance(library_payload, list) or not all(
        isinstance(item, dict) for item in library_payload
    ):
        raise OpenAIAdapterError("library_files.json has an unsupported schema")
    asset_members = {
        PurePosixPath(item.member_path).stem: item.member_path
        for item in members
        if item.accounting_category == "binary_asset"
        and item.member_path.casefold().endswith(".dat")
    }
    library: dict[str, dict[str, Any]] = {}
    for item in library_payload:
        file_id = item.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            raise OpenAIAdapterError("library file record has no stable file_id")
        if file_id in library:
            raise OpenAIAdapterError("library_files.json repeats a file_id")
        library[file_id] = item
    original_names = {
        PurePosixPath(path).stem: value for path, value in names_payload.items() if path in by_name
    }
    return AssetCatalog(
        available_ids=frozenset(asset_members),
        member_paths=asset_members,
        original_names=original_names,
        library_records=library,
    )


def _asset_filename(asset_id: str, member: OpenAIPhysicalMember, catalog: AssetCatalog) -> str:
    library = catalog.library_records.get(asset_id, {})
    for candidate in (
        catalog.original_names.get(asset_id),
        library.get("file_name"),
        library.get("normalized_name"),
        member.filename,
    ):
        if isinstance(candidate, str) and candidate.strip():
            return _postgres_safe_text(PurePosixPath(candidate.replace("\\", "/")).name)
    return member.filename


def parse_asset_member(
    reader: OpenAIExportReader,
    member: OpenAIPhysicalMember,
    catalog: AssetCatalog,
) -> ParsedExport:
    digest = sha256_member(reader, member.member_path)
    asset_id = PurePosixPath(member.member_path).stem
    library = catalog.library_records.get(asset_id)
    filename = _asset_filename(asset_id, member, catalog)
    mime_type = library.get("mime_type") if library else None
    if not isinstance(mime_type, str) or not mime_type:
        mime_type = mimetypes.guess_type(filename)[0]
    created_at = _timestamp(library.get("created_at")) if library else None
    modified_at = _timestamp(library.get("modified_at")) if library else None
    metadata: dict[str, Any] = {
        "openai": {
            "logical_path": member.logical_path,
            "available_in_export": True,
            "conversation_asset_name": catalog.original_names.get(asset_id),
            "library_file": _postgres_safe_json(library),
        }
    }
    asset = NormalizedAsset(
        source=SOURCE,
        asset_namespace=ASSET_NAMESPACE,
        source_asset_id=asset_id,
        sha256=digest,
        mime_type=mime_type,
        filename=filename,
        original_path=reader.locator(member.member_path),
        size_bytes=member.size_bytes,
        created_at=created_at,
        modified_at=modified_at,
        metadata=metadata,
        provider_updated_at=modified_at or created_at,
        authority_at=modified_at or created_at,
        authority_kind="provider_updated_at" if modified_at or created_at else "unknown",
    )
    return ParsedExport(
        source=SOURCE,
        parser_version=PARSER_VERSION,
        category=f"openai/asset/{member.member_id}",
        filename=member.filename,
        original_path=reader.locator(member.member_path),
        sha256=digest,
        size_bytes=member.size_bytes,
        snapshot_kind="partial",
        snapshot_scope="openai_account_export",
        assets=(asset,),
    )


def _generic_record_id(logical_path: str, item: Any, ordinal: int) -> str:
    if isinstance(item, dict):
        for key in ("file_id", "conversation_id", "project_id", "id"):
            value = item.get(key)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                return str(value)
    return f"item:{ordinal:08d}:{_stable_digest(item)[:24]}"


def _generic_record(
    logical_path: str,
    item: Any,
    ordinal: int,
    *,
    duplicate_ids: Counter[str],
) -> NormalizedRecord:
    base_id = _generic_record_id(logical_path, item, ordinal)
    source_id = base_id
    if duplicate_ids[base_id] > 1:
        source_id = f"{base_id}:{ordinal:08d}:{_stable_digest(item)[:16]}"
    source_id = _postgres_safe_text(source_id)
    value = item if isinstance(item, dict) else {"value": item}
    safe_value = _postgres_safe_json(value)
    title = next(
        (
            candidate
            for candidate in (value.get("title"), value.get("name"), value.get("file_name"))
            if isinstance(candidate, str)
        ),
        None,
    )
    created_at = _timestamp(
        value.get("create_time") or value.get("created_at") or value.get("record_creation_time")
    )
    updated_at = _timestamp(value.get("update_time") or value.get("updated_at"))
    namespace = (
        LIBRARY_NAMESPACE
        if PurePosixPath(logical_path).name == "library_files.json"
        else f"chatgpt/structured/{logical_path.casefold()}"
    )
    return NormalizedRecord(
        source=SOURCE,
        record_type=f"openai_{PurePosixPath(logical_path).stem}",
        source_record_namespace=namespace,
        source_record_id=source_id,
        title=_postgres_safe_text(title) if title is not None else None,
        created_at=created_at,
        updated_at=updated_at,
        metadata={"openai": safe_value, "logical_path": logical_path, "ordinal": ordinal},
        provider_updated_at=updated_at or created_at,
        authority_at=updated_at or created_at,
        authority_kind="provider_updated_at" if updated_at or created_at else "unknown",
    )


def parse_structured_member(
    reader: OpenAIExportReader,
    member: OpenAIPhysicalMember,
    catalog: AssetCatalog,
) -> ParsedExport:
    payload, raw = _load_json(reader, member.member_path)
    items = payload if isinstance(payload, list) else [payload]
    base_ids = Counter(
        _generic_record_id(member.logical_path, item, ordinal) for ordinal, item in enumerate(items)
    )
    records = tuple(
        _generic_record(member.logical_path, item, ordinal, duplicate_ids=base_ids)
        for ordinal, item in enumerate(items)
    )
    relationships: list[NormalizedRelationship] = []
    if member.logical_path == "library_files.json":
        for record, item in zip(records, items, strict=True):
            if not isinstance(item, dict):
                continue
            file_id = item.get("file_id")
            if not isinstance(file_id, str) or file_id not in catalog.available_ids:
                continue
            relationships.append(
                _relationship(
                    "library_file_has_export_asset",
                    "record",
                    LIBRARY_NAMESPACE,
                    record.source_record_id,
                    "asset",
                    ASSET_NAMESPACE,
                    file_id,
                    authority_at=record.authority_at,
                    metadata={"openai": {"available_in_export": True}},
                )
            )
    return ParsedExport(
        source=SOURCE,
        parser_version=PARSER_VERSION,
        category=f"openai/structured/{member.member_id}",
        filename=member.filename,
        original_path=reader.locator(member.member_path),
        sha256=_sha256_bytes(raw),
        size_bytes=member.size_bytes,
        snapshot_kind="partial",
        snapshot_scope="openai_account_export",
        records=records,
        relationships=tuple(relationships),
    )


def _text_fragments(value: Any, *, key: str | None = None) -> list[str]:
    if isinstance(value, str):
        return (
            [value]
            if key
            in {
                None,
                "text",
                "content",
                "caption",
                "transcription",
                "summary",
                "parts",
                "thoughts",
            }
            else []
        )
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_text_fragments(item, key=key))
        return result
    if isinstance(value, dict):
        result = []
        for child_key, child in value.items():
            if child_key in {
                "text",
                "content",
                "caption",
                "transcription",
                "summary",
                "parts",
                "thoughts",
            }:
                result.extend(_text_fragments(child, key=child_key))
        return result
    return []


def _message_text(content: Any) -> str | None:
    if not isinstance(content, dict):
        return None
    fragments = [item for item in _text_fragments(content) if item]
    return "\n".join(fragments) or None


def _asset_pointers(value: Any) -> set[str]:
    pointers: set[str] = set()
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str) and item.startswith("sediment://"):
            asset_id = item.removeprefix("sediment://").strip().removesuffix(".dat")
            if asset_id:
                pointers.add(asset_id)
    return pointers


def _conversation_objects(
    raw: Any,
    *,
    shard_name: str,
    catalog: AssetCatalog,
) -> tuple[
    NormalizedConversation,
    tuple[NormalizedAsset, ...],
    tuple[NormalizedRelationship, ...],
    int,
    int,
]:
    if not isinstance(raw, dict):
        raise OpenAIAdapterError("conversation shard contains a non-object conversation")
    conversation_id = raw.get("id") or raw.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id:
        raise OpenAIAdapterError("conversation has no stable id")
    mapping = raw.get("mapping")
    if not isinstance(mapping, dict):
        raise OpenAIAdapterError("conversation mapping must be an object")
    node_messages: dict[str, dict[str, Any]] = {}
    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            raise OpenAIAdapterError("conversation mapping contains a non-object node")
        message = node.get("message")
        if isinstance(message, dict):
            node_messages[str(node_id)] = message

    updated_at = _timestamp(raw.get("update_time"))
    events: list[NormalizedEvent] = []
    assets: dict[str, NormalizedAsset] = {}
    relationships: dict[str, NormalizedRelationship] = {}
    resolved_references = 0
    unavailable_references = 0
    for node_id, message in node_messages.items():
        author = message.get("author") if isinstance(message.get("author"), dict) else {}
        role = author.get("role") if isinstance(author.get("role"), str) else None
        event_id = f"{conversation_id}:{node_id}"
        node = mapping[node_id]
        parent_node_id = node.get("parent") if isinstance(node, dict) else None
        parent_event_id = (
            f"{conversation_id}:{parent_node_id}"
            if isinstance(parent_node_id, str) and parent_node_id in node_messages
            else None
        )
        message_updated_at = _timestamp(message.get("update_time")) or updated_at
        content = message.get("content")
        raw_message_id = message.get("id")
        metadata = {
            "openai": {
                "provider_message_id": str(raw_message_id) if raw_message_id is not None else None,
                "mapping_node_id": node_id,
                "parent_mapping_node_id": parent_node_id,
                "content": content,
                "message_metadata": message.get("metadata"),
                "recipient": message.get("recipient"),
                "status": message.get("status"),
                "end_turn": message.get("end_turn"),
                "weight": message.get("weight"),
                "author": author,
                "shard": shard_name,
            }
        }
        event = NormalizedEvent(
            occurred_at=_timestamp(message.get("create_time")),
            source=SOURCE,
            event_type="conversation_message",
            source_record_namespace=MESSAGE_NAMESPACE,
            actor_identity=role,
            text=(
                _postgres_safe_text(text) if (text := _message_text(content)) is not None else None
            ),
            metadata=_postgres_safe_json(metadata),
            source_record_id=event_id,
            source_node_id=event_id,
            parent_source_node_id=parent_event_id,
            provider_updated_at=message_updated_at,
            authority_at=message_updated_at,
            authority_kind="provider_updated_at" if message_updated_at else "unknown",
        )
        events.append(event)
        if parent_event_id is not None:
            relationship = _relationship(
                "message_has_parent",
                "event",
                MESSAGE_NAMESPACE,
                event_id,
                "event",
                MESSAGE_NAMESPACE,
                parent_event_id,
                authority_at=message_updated_at,
                metadata={"openai": {"parent_mapping_node_id": parent_node_id}},
            )
            relationships[relationship.source_relationship_id] = relationship
        for asset_id in sorted(_asset_pointers([content, message.get("metadata")])):
            available = asset_id in catalog.available_ids
            if available:
                resolved_references += 1
            else:
                unavailable_references += 1
                original_name = catalog.original_names.get(asset_id)
                placeholder = NormalizedAsset(
                    source=SOURCE,
                    asset_namespace=ASSET_NAMESPACE,
                    source_asset_id=asset_id,
                    filename=(
                        _postgres_safe_text(original_name)
                        if original_name is not None
                        else f"{asset_id}.dat"
                    ),
                    original_path=f"sediment://{asset_id}",
                    size_bytes=0,
                    metadata=_postgres_safe_json(
                        {
                            "openai": {
                                "available_in_export": False,
                                "provider_pointer": f"sediment://{asset_id}",
                                "conversation_asset_name": original_name,
                            }
                        }
                    ),
                    provider_updated_at=message_updated_at,
                    authority_at=message_updated_at,
                    authority_kind="provider_updated_at" if message_updated_at else "unknown",
                )
                assets[asset_id] = placeholder
            relationship = _relationship(
                "message_has_asset",
                "event",
                MESSAGE_NAMESPACE,
                event_id,
                "asset",
                ASSET_NAMESPACE,
                asset_id,
                authority_at=message_updated_at,
                metadata={
                    "openai": {
                        "provider_pointer": f"sediment://{asset_id}",
                        "available_in_export": available,
                    }
                },
            )
            relationships[relationship.source_relationship_id] = relationship
    occurred = [event.occurred_at for event in events if event.occurred_at]
    created_at = _timestamp(raw.get("create_time"))
    raw_metadata = {
        key: value
        for key, value in raw.items()
        if key not in {"mapping", "id", "conversation_id", "title", "create_time", "update_time"}
    }
    conversation = NormalizedConversation(
        source=SOURCE,
        source_conversation_id=conversation_id,
        source_record_namespace=CONVERSATION_NAMESPACE,
        title=(_postgres_safe_text(raw["title"]) if isinstance(raw.get("title"), str) else None),
        started_at=min(occurred) if occurred else created_at,
        ended_at=max(occurred) if occurred else created_at,
        metadata={"openai": _postgres_safe_json(raw_metadata), "shard": shard_name},
        events=tuple(events),
        provider_updated_at=updated_at,
        authority_at=updated_at,
        authority_kind="provider_updated_at" if updated_at else "unknown",
    )
    return (
        conversation,
        tuple(assets.values()),
        tuple(relationships.values()),
        resolved_references,
        unavailable_references,
    )


def iter_conversation_member_exports(
    reader: OpenAIExportReader,
    member: OpenAIPhysicalMember,
    catalog: AssetCatalog,
    *,
    seen_conversation_ids: set[str],
    conversations_per_chunk: int = CONVERSATIONS_PER_CHUNK,
):
    if conversations_per_chunk <= 0:
        raise ValueError("conversations_per_chunk must be positive")
    payload, raw = _load_json(reader, member.member_path)
    if not isinstance(payload, list):
        raise OpenAIAdapterError("conversation shard must contain a JSON array")
    digest = _sha256_bytes(raw)
    chunks = [
        payload[index : index + conversations_per_chunk]
        for index in range(0, len(payload), conversations_per_chunk)
    ]
    if not chunks:
        chunks = [[]]
    for chunk_index, raw_conversations in enumerate(chunks, start=1):
        conversations: list[NormalizedConversation] = []
        assets: dict[str, NormalizedAsset] = {}
        relationships: dict[str, NormalizedRelationship] = {}
        resolved_references = unavailable_references = 0
        for raw_conversation in raw_conversations:
            objects = _conversation_objects(
                raw_conversation,
                shard_name=member.filename,
                catalog=catalog,
            )
            conversation, item_assets, item_relationships, resolved, unavailable = objects
            if conversation.source_conversation_id in seen_conversation_ids:
                raise OpenAIAdapterError("conversation id appears in multiple export locations")
            seen_conversation_ids.add(conversation.source_conversation_id)
            conversations.append(conversation)
            assets.update((item.source_asset_id, item) for item in item_assets)
            relationships.update((item.source_relationship_id, item) for item in item_relationships)
            resolved_references += resolved
            unavailable_references += unavailable
        try:
            yield ParsedExport(
                source=SOURCE,
                parser_version=PARSER_VERSION,
                category=(f"openai/conversations/{member.member_id}/chunk/{chunk_index:08d}"),
                filename=member.filename,
                original_path=reader.locator(member.member_path),
                sha256=digest,
                size_bytes=member.size_bytes,
                snapshot_kind="partial",
                snapshot_scope="openai_account_export",
                parse_warnings=(),
                conversations=tuple(conversations),
                assets=tuple(assets.values()),
                relationships=tuple(relationships.values()),
            )
        except ValidationError as exc:
            raise OpenAIAdapterError("normalized conversation shard failed validation") from exc
