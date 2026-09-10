from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

from .models import NormalizedConversation, NormalizedEvent, ParsedExport

SOURCE = "anthropic"
PARSER_VERSION = "anthropic-v1"
SUPPORTED_CATEGORIES = {
    "conversations",
    "design_chats",
    "light_metadata",
    "memories",
    "projects",
}


class AnthropicExportError(ValueError):
    """Raised when an Anthropic export manifest or archive is invalid."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _required_string(record: dict[str, Any], key: str, context: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise AnthropicExportError(f"{context} has no {key}")
    return value


def _anthropic_metadata(**values: Any) -> dict[str, Any]:
    return {"anthropic": {key: value for key, value in values.items() if value is not None}}


def _sha256_json(record: dict[str, Any]) -> str:
    encoded = json.dumps(
        record,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _visible_message_text(content: Any) -> str | None:
    if not isinstance(content, list):
        return None
    parts = [
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
        and block["text"]
    ]
    combined = "\n".join(parts)
    return combined or None


def _load_json_entries(path: Path) -> dict[str, Any]:
    try:
        with ZipFile(path) as archive:
            json_entries = sorted(
                entry for entry in archive.namelist() if entry.lower().endswith(".json")
            )
            if not json_entries:
                raise AnthropicExportError(f"no JSON files found in {path.name}")
            result: dict[str, Any] = {}
            for entry in json_entries:
                try:
                    result[entry] = json.loads(archive.read(entry))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise AnthropicExportError(
                        f"invalid JSON in {path.name}::{entry}"
                    ) from error
            return result
    except BadZipFile as error:
        raise AnthropicExportError(f"invalid ZIP archive: {path}") from error


def _parse_standard_message(
    conversation_id: str,
    message: Any,
    index: int,
    conversation_updated_at: datetime | None,
) -> NormalizedEvent:
    if not isinstance(message, dict):
        raise AnthropicExportError(
            f"conversation {conversation_id} message at index {index} must be an object"
        )
    message_id = _required_string(message, "uuid", f"conversation {conversation_id} message")
    sender = message.get("sender") if isinstance(message.get("sender"), str) else None
    parent_message_id = (
        message.get("parent_message_uuid")
        if isinstance(message.get("parent_message_uuid"), str)
        else None
    )
    content_blocks = message.get("content") if isinstance(message.get("content"), list) else []
    canonical_text = _visible_message_text(content_blocks)
    top_level_text = message.get("text") if isinstance(message.get("text"), str) else None
    provider_updated_at = _timestamp(message.get("updated_at")) or conversation_updated_at
    provider_metadata: dict[str, Any] = {
        "conversation_uuid": conversation_id,
        "source_message_uuid": message_id,
        "source_sender": sender,
        "source_created_at": message.get("created_at"),
        "parent_message_uuid": parent_message_id,
        "canonical_text_source": "content[type=text]",
        "content_blocks": content_blocks,
        "attachments": message.get("attachments")
        if isinstance(message.get("attachments"), list)
        else [],
        "files": message.get("files") if isinstance(message.get("files"), list) else [],
    }
    if top_level_text is not None and top_level_text != (canonical_text or ""):
        provider_metadata["top_level_export_text"] = top_level_text
    return NormalizedEvent(
        occurred_at=_timestamp(message.get("created_at")),
        source=SOURCE,
        event_type="conversation_message",
        source_record_namespace="message",
        actor_identity=sender,
        text=canonical_text,
        metadata={"anthropic": provider_metadata},
        source_record_id=message_id,
        source_node_id=message_id,
        parent_source_node_id=parent_message_id,
        provider_updated_at=provider_updated_at,
        authority_at=provider_updated_at,
        authority_kind=(
            "provider_updated_at" if provider_updated_at is not None else "unknown"
        ),
    )


def _parse_standard_conversations(payload: Any) -> tuple[NormalizedConversation, ...]:
    if not isinstance(payload, list):
        raise AnthropicExportError("conversations.json must contain a JSON array")
    conversations: list[NormalizedConversation] = []
    for index, raw in enumerate(payload):
        if not isinstance(raw, dict):
            raise AnthropicExportError(f"conversation at index {index} must be an object")
        conversation_id = _required_string(raw, "uuid", f"conversation at index {index}")
        messages = raw.get("chat_messages")
        if not isinstance(messages, list):
            raise AnthropicExportError(
                f"conversation {conversation_id} chat_messages must be an array"
            )
        updated_at = _timestamp(raw.get("updated_at"))
        events = tuple(
            _parse_standard_message(
                conversation_id, message, message_index, updated_at
            )
            for message_index, message in enumerate(messages)
        )
        conversations.append(
            NormalizedConversation(
                source=SOURCE,
                source_conversation_id=conversation_id,
                source_record_namespace="chat",
                title=raw.get("name") if isinstance(raw.get("name"), str) else None,
                started_at=_timestamp(raw.get("created_at")),
                ended_at=_timestamp(raw.get("updated_at")),
                metadata=_anthropic_metadata(
                    conversation_uuid=conversation_id,
                    summary=raw.get("summary"),
                    account=raw.get("account"),
                    source_created_at=raw.get("created_at"),
                ),
                events=events,
                provider_updated_at=updated_at,
                authority_at=updated_at,
                authority_kind=(
                    "provider_updated_at" if updated_at is not None else "unknown"
                ),
            )
        )
    return tuple(conversations)


def _parse_design_chat(raw: Any, entry_name: str) -> NormalizedConversation:
    if not isinstance(raw, dict):
        raise AnthropicExportError(f"design chat {entry_name} must be an object")
    conversation_id = _required_string(raw, "uuid", f"design chat {entry_name}")
    project = raw.get("project") if isinstance(raw.get("project"), dict) else {}
    messages = raw.get("messages")
    if not isinstance(messages, list):
        raise AnthropicExportError(f"design chat {conversation_id} messages must be an array")
    conversation_updated_at = _timestamp(raw.get("updated_at"))
    events: list[NormalizedEvent] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise AnthropicExportError(
                f"design chat {conversation_id} message at index {index} must be an object"
            )
        message_id = _required_string(
            message,
            "uuid",
            f"design chat {conversation_id} message at index {index}",
        )
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        role = message.get("role") if isinstance(message.get("role"), str) else None
        text_value = content.get("content")
        events.append(
            NormalizedEvent(
                occurred_at=_timestamp(message.get("created_at")),
                source=SOURCE,
                event_type="conversation_message",
                source_record_namespace="design_message",
                actor_identity=role,
                text=text_value if isinstance(text_value, str) and text_value else None,
                metadata=_anthropic_metadata(
                    design_chat_uuid=conversation_id,
                    project_uuid=project.get("uuid"),
                    project_name=project.get("name"),
                    role=role,
                    source_created_at=message.get("created_at"),
                    content=content,
                ),
                source_record_id=message_id,
                source_node_id=message_id,
                provider_updated_at=conversation_updated_at,
                authority_at=conversation_updated_at,
                authority_kind=(
                    "provider_updated_at"
                    if conversation_updated_at is not None
                    else "unknown"
                ),
            )
        )
    return NormalizedConversation(
        source=SOURCE,
        source_conversation_id=conversation_id,
        source_record_namespace="design_chat",
        title=raw.get("title") if isinstance(raw.get("title"), str) else None,
        started_at=_timestamp(raw.get("created_at")),
        ended_at=_timestamp(raw.get("updated_at")),
        metadata=_anthropic_metadata(
            design_chat_uuid=conversation_id,
            project=project,
            source_created_at=raw.get("created_at"),
        ),
        events=tuple(events),
        provider_updated_at=conversation_updated_at,
        authority_at=conversation_updated_at,
        authority_kind=(
            "provider_updated_at" if conversation_updated_at is not None else "unknown"
        ),
    )


def _parse_profile_event(user: Any, index: int) -> NormalizedEvent:
    if not isinstance(user, dict):
        raise AnthropicExportError(f"user at index {index} must be an object")
    account_id = _required_string(user, "uuid", f"user at index {index}")
    display_name = user.get("full_name") if isinstance(user.get("full_name"), str) else None
    return NormalizedEvent(
        occurred_at=None,
        source=SOURCE,
        event_type="account_profile_snapshot",
        source_record_namespace="account_profile",
        actor_identity=account_id,
        text=display_name,
        metadata=_anthropic_metadata(
            account_uuid=account_id,
            full_name=display_name,
            email_address=user.get("email_address"),
            verified_phone_number=user.get("verified_phone_number"),
        ),
        source_record_id=f"account_profile:{account_id}",
    )


def _parse_login_event(login: Any, index: int) -> NormalizedEvent:
    if not isinstance(login, dict):
        raise AnthropicExportError(f"login event at index {index} must be an object")
    account_id = (
        login.get("account_uuid") if isinstance(login.get("account_uuid"), str) else None
    )
    return NormalizedEvent(
        occurred_at=_timestamp(login.get("timestamp")),
        source=SOURCE,
        event_type="account_login",
        source_record_namespace="account_login",
        actor_identity=account_id,
        metadata=_anthropic_metadata(**login),
        source_record_id=f"login:{_sha256_json(login)}",
    )


def _parse_project_events(raw: Any, entry_name: str) -> tuple[NormalizedEvent, ...]:
    if not isinstance(raw, dict):
        raise AnthropicExportError(f"project {entry_name} must be an object")
    project_id = _required_string(raw, "uuid", f"project {entry_name}")
    creator = raw.get("creator") if isinstance(raw.get("creator"), dict) else {}
    name = raw.get("name") if isinstance(raw.get("name"), str) else None
    description = raw.get("description") if isinstance(raw.get("description"), str) else None
    project_metadata = {
        key: value for key, value in raw.items() if key not in {"docs", "updated_at"}
    }
    events = [
        NormalizedEvent(
            occurred_at=_timestamp(raw.get("updated_at") or raw.get("created_at")),
            source=SOURCE,
            event_type="project_snapshot",
            source_record_namespace="project",
            actor_identity=creator.get("uuid"),
            text=description or None,
            metadata=_anthropic_metadata(**project_metadata),
            source_record_id=f"project:{project_id}",
            provider_updated_at=_timestamp(raw.get("updated_at")),
            authority_at=_timestamp(raw.get("updated_at")),
            authority_kind=(
                "provider_updated_at" if _timestamp(raw.get("updated_at")) else "unknown"
            ),
        )
    ]
    docs = raw.get("docs") if isinstance(raw.get("docs"), list) else []
    for index, document in enumerate(docs):
        if not isinstance(document, dict):
            raise AnthropicExportError(
                f"project {project_id} document at index {index} must be an object"
            )
        document_id = _required_string(
            document,
            "uuid",
            f"project {project_id} document at index {index}",
        )
        content = document.get("content")
        events.append(
            NormalizedEvent(
                occurred_at=_timestamp(document.get("created_at")),
                source=SOURCE,
                event_type="project_document",
                source_record_namespace="project_document",
                actor_identity=creator.get("uuid"),
                text=content if isinstance(content, str) and content else None,
                metadata=_anthropic_metadata(
                    project_uuid=project_id,
                    project_name=name,
                    document_uuid=document_id,
                    filename=document.get("filename"),
                    created_at=document.get("created_at"),
                ),
                source_record_id=f"project_doc:{document_id}",
            )
        )
    return tuple(events)


def _parse_memory_events(raw: Any, entry_name: str) -> tuple[NormalizedEvent, ...]:
    if not isinstance(raw, dict):
        raise AnthropicExportError(f"memory record {entry_name} must be an object")
    account_id = (
        raw.get("account_uuid") if isinstance(raw.get("account_uuid"), str) else None
    )
    events: list[NormalizedEvent] = []
    conversations_memory = raw.get("conversations_memory")
    if isinstance(conversations_memory, str):
        events.append(
            NormalizedEvent(
                occurred_at=None,
                source=SOURCE,
                event_type="memory_snapshot",
                source_record_namespace="memory",
                actor_identity=account_id,
                text=conversations_memory or None,
                metadata=_anthropic_metadata(
                    account_uuid=account_id,
                    memory_scope="conversations",
                ),
                source_record_id="memory:conversations",
            )
        )
    project_memories = raw.get("project_memories")
    if isinstance(project_memories, dict):
        for project_id, content in sorted(project_memories.items()):
            events.append(
                NormalizedEvent(
                    occurred_at=None,
                    source=SOURCE,
                    event_type="memory_snapshot",
                    source_record_namespace="memory",
                    actor_identity=account_id,
                    text=content if isinstance(content, str) and content else None,
                    metadata=_anthropic_metadata(
                        account_uuid=account_id,
                        memory_scope="project",
                        project_uuid=project_id,
                    ),
                    source_record_id=f"memory:project:{project_id}",
                )
            )
    memory_files = raw.get("memory_files")
    if isinstance(memory_files, list):
        for index, memory_file in enumerate(memory_files):
            if not isinstance(memory_file, dict):
                raise AnthropicExportError(
                    f"memory file at index {index} in {entry_name} must be an object"
                )
            path = _required_string(
                memory_file,
                "path",
                f"memory file at index {index} in {entry_name}",
            )
            content = memory_file.get("content")
            events.append(
                NormalizedEvent(
                    occurred_at=_timestamp(memory_file.get("updated_at")),
                    source=SOURCE,
                    event_type="memory_file_snapshot",
                    source_record_namespace="memory_file",
                    actor_identity=account_id,
                    text=content if isinstance(content, str) and content else None,
                    metadata=_anthropic_metadata(
                        account_uuid=account_id,
                        path=path,
                    ),
                    source_record_id=f"memory:file:{path}",
                    provider_updated_at=_timestamp(memory_file.get("updated_at")),
                    authority_at=_timestamp(memory_file.get("updated_at")),
                    authority_kind=(
                        "provider_updated_at"
                        if _timestamp(memory_file.get("updated_at"))
                        else "unknown"
                    ),
                )
            )
    return tuple(events)


def _parse_category(category: str, entries: dict[str, Any]) -> tuple[
    tuple[NormalizedConversation, ...], tuple[NormalizedEvent, ...]
]:
    if category == "conversations":
        candidates = [
            value
            for name, value in entries.items()
            if Path(name).name == "conversations.json"
        ]
        if len(candidates) != 1:
            raise AnthropicExportError(
                f"expected exactly one conversations.json; found {len(candidates)}"
            )
        return _parse_standard_conversations(candidates[0]), ()
    if category == "design_chats":
        return tuple(
            _parse_design_chat(value, name) for name, value in sorted(entries.items())
        ), ()
    if category == "light_metadata":
        users = next(
            (value for name, value in entries.items() if Path(name).name == "users.json"),
            None,
        )
        login_history = next(
            (
                value
                for name, value in entries.items()
                if Path(name).name == "login_history.json"
            ),
            None,
        )
        if not isinstance(users, list) or not isinstance(login_history, dict):
            raise AnthropicExportError(
                "light_metadata archive requires users.json and login_history.json"
            )
        login_events = login_history.get("login_events")
        if not isinstance(login_events, list):
            raise AnthropicExportError("login_history.json login_events must be an array")
        events = tuple(_parse_profile_event(user, index) for index, user in enumerate(users))
        events += tuple(
            _parse_login_event(login, index) for index, login in enumerate(login_events)
        )
        return (), events
    if category == "projects":
        return (), tuple(
            event
            for name, value in sorted(entries.items())
            for event in _parse_project_events(value, name)
        )
    if category == "memories":
        return (), tuple(
            event
            for name, value in sorted(entries.items())
            for event in _parse_memory_events(value, name)
        )
    raise AnthropicExportError(f"unsupported Anthropic export category: {category}")


def _validate_unique_records(parsed_exports: tuple[ParsedExport, ...]) -> None:
    conversations: set[tuple[str, str]] = set()
    events: set[tuple[str, str]] = set()
    for parsed in parsed_exports:
        for conversation in parsed.conversations:
            conversation_key = (
                conversation.source_record_namespace,
                conversation.source_conversation_id,
            )
            if conversation_key in conversations:
                raise AnthropicExportError(
                    "duplicate Anthropic conversation id in export: "
                    f"{conversation_key}"
                )
            conversations.add(conversation_key)
            for event in conversation.events:
                event_key = (event.source_record_namespace, event.source_record_id)
                if event_key in events:
                    raise AnthropicExportError(
                        f"duplicate Anthropic record id in export: {event_key}"
                    )
                events.add(event_key)
        for event in parsed.events:
            event_key = (event.source_record_namespace, event.source_record_id)
            if event_key in events:
                raise AnthropicExportError(
                    f"duplicate Anthropic record id in export: {event_key}"
                )
            events.add(event_key)


def parse_anthropic_export(manifest_path: str | Path) -> tuple[ParsedExport, ...]:
    path = Path(manifest_path).expanduser().resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != ".json":
        raise AnthropicExportError("source must be an Anthropic export manifest JSON file")
    try:
        manifest = json.loads(path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnthropicExportError(f"invalid Anthropic export manifest: {path}") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("data_files"), list):
        raise AnthropicExportError("Anthropic manifest data_files must be an array")
    exported_at = _timestamp(manifest.get("created_at"))
    manifest_directory = path.parent.resolve()
    parsed_exports: list[ParsedExport] = []
    filenames: set[str] = set()
    try:
        for index, item in enumerate(manifest["data_files"]):
            if not isinstance(item, dict):
                raise AnthropicExportError(f"manifest data file at index {index} must be an object")
            category = _required_string(item, "category", f"manifest data file at index {index}")
            if category not in SUPPORTED_CATEGORIES:
                raise AnthropicExportError(
                    f"unsupported Anthropic export category: {category}"
                )
            filename = _required_string(item, "filename", f"manifest data file at index {index}")
            if filename in filenames:
                raise AnthropicExportError(f"duplicate filename in Anthropic manifest: {filename}")
            filenames.add(filename)
            if Path(filename).name != filename:
                raise AnthropicExportError(f"unsafe filename in Anthropic manifest: {filename}")
            archive_path = (manifest_directory / filename).resolve(strict=True)
            if archive_path.parent != manifest_directory or archive_path.suffix.lower() != ".zip":
                raise AnthropicExportError(f"unsafe archive path in Anthropic manifest: {filename}")
            entries = _load_json_entries(archive_path)
            conversations, events = _parse_category(category, entries)
            parsed_exports.append(
                ParsedExport(
                    source=SOURCE,
                    parser_version=PARSER_VERSION,
                    filename=filename,
                    original_path=str(archive_path),
                    sha256=_sha256_file(archive_path),
                    size_bytes=archive_path.stat().st_size,
                    exported_at=exported_at,
                    export_id=(
                        str(manifest.get("uuid"))
                        if manifest.get("uuid") is not None
                        else None
                    ),
                    snapshot_kind="complete",
                    snapshot_scope="account",
                    conversations=conversations,
                    events=events,
                )
            )
    except FileNotFoundError as error:
        raise AnthropicExportError(f"Anthropic source archive is missing: {error.filename}") from error
    parsed = tuple(parsed_exports)
    _validate_unique_records(parsed)
    return parsed
