from __future__ import annotations

import hashlib
import json
import mimetypes
from collections import Counter
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import ZipFile

from ..anthropic_importer import (
    SOURCE,
    AnthropicExportError,
    _parse_category,
    _parse_standard_conversations,
)
from ..fingerprints import canonical_json_bytes
from ..models import (
    NormalizedAsset,
    NormalizedRecord,
    NormalizedRelationship,
    ParsedExport,
)
from .inventory import AnthropicArchive, AnthropicInventory, AnthropicMember

PARSER_VERSION = "anthropic-v2"
SNAPSHOT_SCOPE = "anthropic_account_export"
CONVERSATIONS_PER_CHUNK = 25
ATTACHMENT_NAMESPACE = "claude/attachment"
EXPORT_MEMBER_NAMESPACE = "claude/export_member"
RELATIONSHIP_NAMESPACE = "claude/relationship"
MANIFEST_NAMESPACE = "claude/export_manifest"


class AnthropicAdapterError(ValueError):
    """Raised when an accounted Anthropic member cannot be normalized safely."""


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_member(archive: ZipFile, member: AnthropicMember) -> bytes:
    try:
        raw = archive.read(member.member_path)
    except KeyError as exc:
        raise AnthropicAdapterError("accounted member disappeared after inventory") from exc
    if hashlib.sha256(raw).hexdigest() != member.sha256:
        raise AnthropicAdapterError("accounted member changed after inventory")
    return raw


def _load_json(archive: ZipFile, member: AnthropicMember) -> tuple[Any, bytes]:
    raw = _read_member(archive, member)
    try:
        return json.loads(raw), raw
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnthropicAdapterError("accounted JSON member is invalid") from exc


def _base_export(
    inventory: AnthropicInventory,
    archive: AnthropicArchive,
    *,
    category: str,
    conversations=(),
    events=(),
    records=(),
    assets=(),
    relationships=(),
    parse_warnings=(),
) -> ParsedExport:
    return ParsedExport(
        source=SOURCE,
        parser_version=PARSER_VERSION,
        category=category,
        filename=archive.filename,
        original_path=archive.source_path,
        sha256=archive.sha256,
        size_bytes=archive.size_bytes,
        exported_at=inventory.exported_at,
        export_id=inventory.export_id,
        snapshot_kind="partial",
        snapshot_scope=SNAPSHOT_SCOPE,
        parse_warnings=tuple(parse_warnings),
        conversations=tuple(conversations),
        events=tuple(events),
        records=tuple(records),
        assets=tuple(assets),
        relationships=tuple(relationships),
    )


def parse_manifest_export(inventory: AnthropicInventory) -> ParsedExport:
    if _sha256_file(inventory.manifest_path) != inventory.manifest_sha256:
        raise AnthropicAdapterError("manifest changed after inventory")
    manifest_record_id = inventory.export_id or inventory.manifest_sha256
    record = NormalizedRecord(
        source=SOURCE,
        record_type="anthropic_export_manifest",
        source_record_namespace=MANIFEST_NAMESPACE,
        source_record_id=manifest_record_id,
        occurred_at=inventory.exported_at,
        metadata={
            "anthropic": {
                "manifest_id": inventory.manifest_id,
                "declared_archives": inventory.declared_archives,
                "archive_categories": [item.category for item in inventory.archives],
                "archive_ids": [item.archive_id for item in inventory.archives],
            }
        },
        provider_updated_at=inventory.exported_at,
        authority_at=inventory.exported_at,
        authority_kind="exported_at" if inventory.exported_at else "unknown",
    )
    return ParsedExport(
        source=SOURCE,
        parser_version=PARSER_VERSION,
        category="anthropic/manifest",
        filename=PurePosixPath(inventory.manifest_path).name,
        original_path=inventory.manifest_path,
        sha256=inventory.manifest_sha256,
        size_bytes=inventory.manifest_size_bytes,
        exported_at=inventory.exported_at,
        export_id=inventory.export_id,
        snapshot_kind="partial",
        snapshot_scope=SNAPSHOT_SCOPE,
        records=(record,),
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
    authority_at,
    metadata: dict[str, Any] | None = None,
) -> NormalizedRelationship:
    identity = {
        "type": relationship_type,
        "subject": [subject_kind, subject_namespace, subject_id],
        "object": [object_kind, object_namespace, object_id],
    }
    return NormalizedRelationship(
        source=SOURCE,
        relationship_type=relationship_type,
        source_relationship_namespace=RELATIONSHIP_NAMESPACE,
        source_relationship_id=f"relationship:{_digest(identity)[:32]}",
        subject_kind=subject_kind,
        subject_source=SOURCE,
        subject_namespace=subject_namespace,
        subject_source_id=subject_id,
        object_kind=object_kind,
        object_source=SOURCE,
        object_namespace=object_namespace,
        object_source_id=object_id,
        metadata=metadata or {},
        provider_updated_at=authority_at,
        authority_at=authority_at,
        authority_kind="provider_updated_at" if authority_at else "unknown",
    )


def _reference_provider_id(reference: dict[str, Any]) -> tuple[str, str] | None:
    for key in ("id", "uuid", "file_id", "file_uuid", "attachment_id"):
        value = reference.get(key)
        if isinstance(value, str) and value:
            return key, value
    return None


def _reference_filename(reference: dict[str, Any], ordinal: int) -> str:
    for key in ("file_name", "filename", "name"):
        value = reference.get(key)
        if isinstance(value, str) and value:
            return PurePosixPath(value.replace("\\", "/")).name or f"reference-{ordinal}"
    return f"reference-{ordinal}"


def _available_member(
    reference: dict[str, Any],
    available_by_path: dict[str, AnthropicMember],
) -> AnthropicMember | None:
    for key in ("path", "file_path"):
        value = reference.get(key)
        if not isinstance(value, str) or not value:
            continue
        normalized = PurePosixPath(value.replace("\\", "/").lstrip("/")).as_posix()
        member = available_by_path.get(normalized)
        if member is not None:
            return member
    return None


def _export_asset_id(member: AnthropicMember) -> str:
    return f"sha256:{member.sha256}"


def _message_graph(
    raw_conversations: list[dict[str, Any]],
    conversations,
    *,
    available_by_path: dict[str, AnthropicMember],
) -> tuple[tuple[NormalizedAsset, ...], tuple[NormalizedRelationship, ...]]:
    assets: dict[tuple[str, str], NormalizedAsset] = {}
    relationships: dict[str, NormalizedRelationship] = {}
    all_message_ids = {
        message.get("uuid")
        for raw in raw_conversations
        for message in raw.get("chat_messages", [])
        if isinstance(message, dict) and isinstance(message.get("uuid"), str)
    }
    for raw, conversation in zip(raw_conversations, conversations, strict=True):
        raw_messages = raw.get("chat_messages")
        if not isinstance(raw_messages, list):
            continue
        event_by_id = {event.source_record_id: event for event in conversation.events}
        for raw_message in raw_messages:
            if not isinstance(raw_message, dict):
                continue
            message_id = raw_message.get("uuid")
            if not isinstance(message_id, str) or message_id not in event_by_id:
                continue
            event = event_by_id[message_id]
            parent_id = raw_message.get("parent_message_uuid")
            if isinstance(parent_id, str) and parent_id in all_message_ids:
                relation = _relationship(
                    "message_has_parent",
                    "event",
                    event.source_record_namespace,
                    message_id,
                    "event",
                    event.source_record_namespace,
                    parent_id,
                    authority_at=event.authority_at,
                    metadata={"anthropic": {"provider_parent_message_uuid": parent_id}},
                )
                relationships[relation.source_relationship_id] = relation
            for reference_kind in ("attachments", "files"):
                references = raw_message.get(reference_kind)
                if not isinstance(references, list):
                    continue
                for ordinal, reference in enumerate(references):
                    if not isinstance(reference, dict):
                        continue
                    available = _available_member(reference, available_by_path)
                    if available is not None:
                        namespace = EXPORT_MEMBER_NAMESPACE
                        asset_id = _export_asset_id(available)
                        filename = available.filename
                        asset = None
                    else:
                        namespace = ATTACHMENT_NAMESPACE
                        explicit = _reference_provider_id(reference)
                        identity = (
                            [explicit[0], explicit[1]]
                            if explicit is not None
                            else [
                                conversation.source_conversation_id,
                                message_id,
                                reference_kind,
                                ordinal,
                            ]
                        )
                        asset_id = f"reference:{_digest(identity)[:32]}"
                        filename = _reference_filename(reference, ordinal)
                        size = reference.get("size_bytes", reference.get("size", 0))
                        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                            size = 0
                        asset = NormalizedAsset(
                            source=SOURCE,
                            asset_namespace=namespace,
                            source_asset_id=asset_id,
                            filename=filename,
                            original_path=f"anthropic-reference://{asset_id}",
                            size_bytes=size,
                            created_at=event.occurred_at,
                            metadata={
                                "anthropic": {
                                    "available_in_export": False,
                                    "reference_kind": reference_kind,
                                    "provider_id_present": explicit is not None,
                                }
                            },
                            provider_updated_at=event.provider_updated_at,
                            authority_at=event.authority_at,
                            authority_kind=event.authority_kind,
                        )
                    if asset is not None:
                        assets[(namespace, asset_id)] = asset
                    relation = _relationship(
                        "message_has_attachment"
                        if reference_kind == "attachments"
                        else "message_has_file",
                        "event",
                        event.source_record_namespace,
                        message_id,
                        "asset",
                        namespace,
                        asset_id,
                        authority_at=event.authority_at,
                        metadata={
                            "anthropic": {
                                "reference_kind": reference_kind,
                                "available_in_export": available is not None,
                                "filename_present": bool(filename),
                            }
                        },
                    )
                    relationships[relation.source_relationship_id] = relation
    return tuple(assets.values()), tuple(relationships.values())


def _track_unique(
    parsed: ParsedExport,
    *,
    seen_conversations: set[tuple[str, str]],
    seen_events: set[tuple[str, str]],
) -> None:
    for conversation in parsed.conversations:
        key = (conversation.source_record_namespace, conversation.source_conversation_id)
        if key in seen_conversations:
            raise AnthropicAdapterError("conversation identity appears more than once")
        seen_conversations.add(key)
        for event in conversation.events:
            event_key = (event.source_record_namespace, event.source_record_id)
            if event_key in seen_events:
                raise AnthropicAdapterError("event identity appears more than once")
            seen_events.add(event_key)
    for event in parsed.events:
        key = (event.source_record_namespace, event.source_record_id)
        if key in seen_events:
            raise AnthropicAdapterError("event identity appears more than once")
        seen_events.add(key)


def _generic_records(member: AnthropicMember, payload: Any) -> tuple[NormalizedRecord, ...]:
    items = payload if isinstance(payload, list) else [payload]
    base_ids: list[str] = []
    for ordinal, item in enumerate(items):
        if isinstance(item, dict):
            value = next(
                (
                    str(item[key])
                    for key in ("uuid", "id")
                    if isinstance(item.get(key), (str, int)) and not isinstance(item.get(key), bool)
                ),
                None,
            )
        else:
            value = None
        base_ids.append(value or f"item:{ordinal:08d}:{_digest(item)[:20]}")
    counts = Counter(base_ids)
    records = []
    for ordinal, (base_id, item) in enumerate(zip(base_ids, items, strict=True)):
        source_id = (
            base_id if counts[base_id] == 1 else f"{base_id}:{ordinal:08d}:{_digest(item)[:12]}"
        )
        records.append(
            NormalizedRecord(
                source=SOURCE,
                record_type="anthropic_structured_json",
                source_record_namespace=f"claude/structured/{member.category}/{member.member_id}",
                source_record_id=source_id,
                metadata={"anthropic": {"payload": item}},
            )
        )
    return tuple(records)


def _binary_export(
    inventory: AnthropicInventory,
    archive: AnthropicArchive,
    member: AnthropicMember,
) -> ParsedExport:
    asset = NormalizedAsset(
        source=SOURCE,
        asset_namespace=EXPORT_MEMBER_NAMESPACE,
        source_asset_id=_export_asset_id(member),
        sha256=member.sha256,
        mime_type=mimetypes.guess_type(member.filename)[0],
        filename=member.filename,
        original_path=f"{archive.source_path}!/{member.member_path}",
        size_bytes=member.size_bytes,
        metadata={
            "anthropic": {
                "available_in_export": True,
                "archive_id": archive.archive_id,
                "member_id": member.member_id,
            }
        },
        provider_updated_at=inventory.exported_at,
        authority_at=inventory.exported_at,
        authority_kind="exported_at" if inventory.exported_at else "unknown",
    )
    return _base_export(
        inventory,
        archive,
        category=f"anthropic/{archive.category}/{member.member_id}",
        assets=(asset,),
    )


def iter_archive_exports(
    inventory: AnthropicInventory,
    archive_info: AnthropicArchive,
    *,
    seen_conversations: set[tuple[str, str]],
    seen_events: set[tuple[str, str]],
    conversations_per_chunk: int = CONVERSATIONS_PER_CHUNK,
) -> Iterator[tuple[AnthropicMember, ParsedExport]]:
    if conversations_per_chunk <= 0:
        raise ValueError("conversations_per_chunk must be positive")
    if _sha256_file(archive_info.source_path) != archive_info.sha256:
        raise AnthropicAdapterError("archive changed after inventory")
    binary_members = {
        item.member_path: item
        for item in archive_info.members
        if item.accounting_category == "binary_asset"
    }
    json_members = [
        item for item in archive_info.members if item.accounting_category == "structured_json"
    ]
    with ZipFile(archive_info.source_path) as archive:
        for member in sorted(binary_members.values(), key=lambda item: item.member_path):
            _read_member(archive, member)
            yield member, _binary_export(inventory, archive_info, member)

        if archive_info.category == "conversations":
            conversation_members = [
                item
                for item in json_members
                if PurePosixPath(item.member_path).name == "conversations.json"
            ]
            if len(conversation_members) != 1:
                raise AnthropicAdapterError(
                    "conversations archive must contain exactly one conversations.json"
                )
            member = conversation_members[0]
            payload, _ = _load_json(archive, member)
            if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
                raise AnthropicAdapterError("conversations.json must contain an object array")
            all_paths = binary_members
            chunks = [
                payload[index : index + conversations_per_chunk]
                for index in range(0, len(payload), conversations_per_chunk)
            ] or [[]]
            for chunk_index, raw_chunk in enumerate(chunks, start=1):
                try:
                    conversations = _parse_standard_conversations(raw_chunk)
                except AnthropicExportError as exc:
                    raise AnthropicAdapterError("conversation chunk failed normalization") from exc
                assets, relationships = _message_graph(
                    raw_chunk,
                    conversations,
                    available_by_path=all_paths,
                )
                parsed = _base_export(
                    inventory,
                    archive_info,
                    category=(
                        f"anthropic/conversations/{member.member_id}/chunk/{chunk_index:08d}"
                    ),
                    conversations=conversations,
                    assets=assets,
                    relationships=relationships,
                )
                _track_unique(
                    parsed,
                    seen_conversations=seen_conversations,
                    seen_events=seen_events,
                )
                yield member, parsed
            for extra in sorted(
                (item for item in json_members if item != member),
                key=lambda item: item.member_path,
            ):
                payload, _ = _load_json(archive, extra)
                yield (
                    extra,
                    _base_export(
                        inventory,
                        archive_info,
                        category=f"anthropic/conversations/{extra.member_id}/generic",
                        records=_generic_records(extra, payload),
                        parse_warnings=(
                            "unrecognized conversations archive JSON preserved generically",
                        ),
                    ),
                )
            return

        if archive_info.category == "light_metadata":
            entries = {
                member.member_path: _load_json(archive, member)[0] for member in json_members
            }
            try:
                conversations, events = _parse_category("light_metadata", entries)
            except AnthropicExportError as exc:
                raise AnthropicAdapterError("light metadata failed normalization") from exc
            primary = json_members[0]
            parsed = _base_export(
                inventory,
                archive_info,
                category=f"anthropic/light_metadata/{archive_info.archive_id}",
                conversations=conversations,
                events=events,
            )
            _track_unique(
                parsed,
                seen_conversations=seen_conversations,
                seen_events=seen_events,
            )
            yield primary, parsed
            expected_names = {"users.json", "login_history.json"}
            for extra in sorted(
                (
                    item
                    for item in json_members
                    if PurePosixPath(item.member_path).name not in expected_names
                ),
                key=lambda item: item.member_path,
            ):
                payload, _ = _load_json(archive, extra)
                yield (
                    extra,
                    _base_export(
                        inventory,
                        archive_info,
                        category=f"anthropic/light_metadata/{extra.member_id}/generic",
                        records=_generic_records(extra, payload),
                        parse_warnings=("unrecognized light-metadata JSON preserved generically",),
                    ),
                )
            return

        for member in sorted(json_members, key=lambda item: item.member_path):
            payload, _ = _load_json(archive, member)
            try:
                conversations, events = _parse_category(
                    archive_info.category, {member.member_path: payload}
                )
            except AnthropicExportError as exc:
                raise AnthropicAdapterError("accounted member failed normalization") from exc
            relationships: list[NormalizedRelationship] = []
            if archive_info.category == "projects":
                projects = {
                    event.metadata.get("anthropic", {}).get("uuid"): event
                    for event in events
                    if event.source_record_namespace == "project"
                }
                for event in events:
                    project_id = event.metadata.get("anthropic", {}).get("project_uuid")
                    project = projects.get(project_id)
                    if event.source_record_namespace == "project_document" and project is not None:
                        relationships.append(
                            _relationship(
                                "project_has_document",
                                "event",
                                project.source_record_namespace,
                                project.source_record_id,
                                "event",
                                event.source_record_namespace,
                                event.source_record_id,
                                authority_at=project.authority_at,
                                metadata={"anthropic": {"project_uuid": project_id}},
                            )
                        )
            if archive_info.category == "design_chats":
                for conversation in conversations:
                    project = conversation.metadata.get("anthropic", {}).get("project")
                    project_id = project.get("uuid") if isinstance(project, dict) else None
                    if isinstance(project_id, str) and project_id:
                        relationships.append(
                            _relationship(
                                "design_chat_in_project",
                                "conversation",
                                conversation.source_record_namespace,
                                conversation.source_conversation_id,
                                "event",
                                "project",
                                f"project:{project_id}",
                                authority_at=conversation.authority_at,
                                metadata={"anthropic": {"project_uuid": project_id}},
                            )
                        )
            parsed = _base_export(
                inventory,
                archive_info,
                category=f"anthropic/{archive_info.category}/{member.member_id}",
                conversations=conversations,
                events=events,
                relationships=relationships,
            )
            _track_unique(
                parsed,
                seen_conversations=seen_conversations,
                seen_events=seen_events,
            )
            yield member, parsed
