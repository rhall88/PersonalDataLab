from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from pydantic import ValidationError

from .models import NormalizedConversation, NormalizedEvent, ParsedExport

SOURCE = "openai"
PARSER_VERSION = "openai-v1"


class OpenAIExportError(ValueError):
    """Raised when an OpenAI export cannot be parsed safely."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_directory_file(path: Path) -> str:
    # An extracted directory has no single immutable byte representation. The
    # conversations file is therefore the provenance unit for directory imports.
    return _sha256_file(path)


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    try:
        return datetime.fromtimestamp(numeric, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _text_from_part(part: Any) -> str | None:
    if isinstance(part, str):
        return part
    if not isinstance(part, dict):
        return None
    for key in ("text", "content", "caption"):
        value = part.get(key)
        if isinstance(value, str):
            return value
    return None


def _message_text(content: Any) -> str | None:
    if not isinstance(content, dict):
        return None
    parts = content.get("parts")
    if not isinstance(parts, list):
        text = content.get("text")
        return text if isinstance(text, str) else None
    values = [text for part in parts if (text := _text_from_part(part)) is not None]
    combined = "\n".join(value for value in values if value)
    return combined or None


def _iter_messages(conversation: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any], str | None]]:
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        raise OpenAIExportError("conversation mapping must be an object")
    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if isinstance(message, dict):
            yield str(node_id), message, str(node.get("parent")) if node.get("parent") else None


def _normalize_conversation(raw: Any, index: int) -> NormalizedConversation:
    if not isinstance(raw, dict):
        raise OpenAIExportError(f"conversation at index {index} must be an object")
    conversation_id = raw.get("id") or raw.get("conversation_id")
    if not conversation_id:
        raise OpenAIExportError(f"conversation at index {index} has no id")

    updated_at = _timestamp(raw.get("update_time"))
    events: list[NormalizedEvent] = []
    for node_id, message, parent_id in _iter_messages(raw):
        author = message.get("author") if isinstance(message.get("author"), dict) else {}
        role = author.get("role") if isinstance(author.get("role"), str) else None
        raw_message_id = message.get("id")
        message_id = str(raw_message_id) if raw_message_id else node_id
        message_updated_at = _timestamp(message.get("update_time")) or updated_at
        content = message.get("content")
        content_type = content.get("content_type") if isinstance(content, dict) else None
        metadata = {
            "author_role": role,
            "author_name": author.get("name"),
            "content_type": content_type,
            "recipient": message.get("recipient"),
            "status": message.get("status"),
        }
        if not raw_message_id:
            metadata["source_record_id_fallback"] = "mapping_node_id"
        metadata = {key: value for key, value in metadata.items() if value is not None}
        events.append(
            NormalizedEvent(
                occurred_at=_timestamp(message.get("create_time")),
                source=SOURCE,
                event_type="conversation_message",
                source_record_namespace="message",
                actor_identity=role,
                text=_message_text(content),
                metadata=metadata,
                source_record_id=message_id,
                source_node_id=node_id,
                parent_source_node_id=parent_id,
                provider_updated_at=message_updated_at,
                authority_at=message_updated_at,
                authority_kind=(
                    "provider_updated_at" if message_updated_at is not None else "unknown"
                ),
            )
        )

    occurred = [event.occurred_at for event in events if event.occurred_at is not None]
    created_at = _timestamp(raw.get("create_time"))
    return NormalizedConversation(
        source=SOURCE,
        source_conversation_id=str(conversation_id),
        source_record_namespace="chat",
        title=raw.get("title") if isinstance(raw.get("title"), str) else None,
        started_at=min(occurred) if occurred else created_at,
        ended_at=max(occurred) if occurred else updated_at or created_at,
        metadata={
            key: raw[key]
            for key in ("is_archived", "default_model_slug", "conversation_template_id")
            if key in raw and raw[key] is not None
        },
        events=tuple(events),
        provider_updated_at=updated_at,
        authority_at=updated_at,
        authority_kind="provider_updated_at" if updated_at is not None else "unknown",
    )


def _find_conversations_json(directory: Path) -> Path:
    candidates = sorted(directory.rglob("conversations.json"))
    if not candidates:
        raise OpenAIExportError(f"no conversations.json found under {directory}")
    if len(candidates) > 1:
        raise OpenAIExportError(f"multiple conversations.json files found under {directory}")
    return candidates[0]


def _load_source(path: Path) -> tuple[list[Any], str, int, str, str]:
    if path.is_dir():
        json_path = _find_conversations_json(path)
        raw_bytes = json_path.read_bytes()
        provenance_path = json_path
        sha256 = _sha256_directory_file(json_path)
    elif path.is_file() and path.suffix.lower() == ".zip":
        try:
            with ZipFile(path) as archive:
                candidates = [name for name in archive.namelist() if Path(name).name == "conversations.json"]
                if len(candidates) != 1:
                    raise OpenAIExportError(
                        f"expected exactly one conversations.json in {path}; found {len(candidates)}"
                    )
                raw_bytes = archive.read(candidates[0])
        except BadZipFile as error:
            raise OpenAIExportError(f"invalid ZIP archive: {path}") from error
        provenance_path = path
        sha256 = _sha256_file(path)
    elif path.is_file() and path.name.lower() == "conversations.json":
        raw_bytes = path.read_bytes()
        provenance_path = path
        sha256 = _sha256_file(path)
    else:
        raise OpenAIExportError("source must be an OpenAI ZIP, extracted directory, or conversations.json")

    try:
        payload = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OpenAIExportError(f"invalid conversations JSON in {path}") from error
    if not isinstance(payload, list):
        raise OpenAIExportError("conversations.json must contain a JSON array")
    return (
        payload,
        sha256,
        provenance_path.stat().st_size,
        provenance_path.name,
        str(provenance_path),
    )


def parse_openai_export(source_path: str | Path) -> ParsedExport:
    path = Path(source_path).expanduser().resolve(strict=True)
    payload, sha256, size_bytes, filename, original_path = _load_source(path)
    try:
        conversations = tuple(
            _normalize_conversation(conversation, index)
            for index, conversation in enumerate(payload)
        )
        seen_record_ids: set[str] = set()
        for conversation in conversations:
            for event in conversation.events:
                if event.source_record_id in seen_record_ids:
                    raise OpenAIExportError(
                        f"duplicate OpenAI message id in export: {event.source_record_id}"
                    )
                seen_record_ids.add(event.source_record_id)
        return ParsedExport(
            source=SOURCE,
            parser_version=PARSER_VERSION,
            filename=filename,
            original_path=original_path,
            sha256=sha256,
            size_bytes=size_bytes,
            snapshot_kind="complete",
            snapshot_scope="account",
            conversations=conversations,
        )
    except ValidationError as error:
        raise OpenAIExportError(f"normalized export failed validation: {error}") from error
