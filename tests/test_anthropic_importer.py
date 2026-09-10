from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

import pytest

from personal_data_lab.anthropic_importer import (
    AnthropicExportError,
    parse_anthropic_export,
)
from personal_data_lab.fingerprints import event_fingerprint


def _write_zip(path: Path, entries: dict[str, object]) -> None:
    with ZipFile(path, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, json.dumps(payload))


def _write_export(directory: Path) -> Path:
    directory.mkdir()
    archives: list[tuple[str, str]] = [
        ("light_metadata", "light_metadata-000.zip"),
        ("projects", "projects-000.zip"),
        ("memories", "memories-000.zip"),
        ("design_chats", "design_chats-000.zip"),
        ("conversations", "conversations-000.zip"),
    ]
    _write_zip(
        directory / "light_metadata-000.zip",
        {
            "users.json": [
                {
                    "uuid": "account-1",
                    "full_name": "Test Person",
                    "email_address": "test@example.invalid",
                    "verified_phone_number": "+15555550100",
                }
            ],
            "login_history.json": {
                "login_events": [
                    {
                        "account_uuid": "account-1",
                        "timestamp": "2026-09-01T12:00:00Z",
                        "ip_address": "192.0.2.1",
                    }
                ]
            },
        },
    )
    _write_zip(
        directory / "projects-000.zip",
        {
            "projects/project-1.json": {
                "uuid": "project-1",
                "name": "Test Project",
                "description": "Project description",
                "created_at": "2026-08-01T00:00:00Z",
                "updated_at": "2026-09-01T00:00:00Z",
                "creator": {"uuid": "account-1"},
                "docs": [
                    {
                        "uuid": "document-1",
                        "filename": "notes.txt",
                        "content": "Project notes",
                        "created_at": "2026-08-02T00:00:00Z",
                    }
                ],
            }
        },
    )
    _write_zip(
        directory / "memories-000.zip",
        {
            "memories.json": {
                "account_uuid": "account-1",
                "conversations_memory": "General memory",
                "project_memories": {"project-1": "Project memory"},
                "memory_files": [
                    {
                        "path": "memory/notes.txt",
                        "content": "Memory file body",
                        "updated_at": "2026-09-02T00:00:00Z",
                    }
                ],
            }
        },
    )
    _write_zip(
        directory / "design_chats-000.zip",
        {
            "design_chats/design-1.json": {
                "uuid": "design-1",
                "title": "Design chat",
                "created_at": "2026-09-03T00:00:00Z",
                "updated_at": "2026-09-03T01:00:00Z",
                "project": {"uuid": "project-1", "name": "Test Project"},
                "messages": [
                    {
                        "uuid": "design-message-1",
                        "role": "user",
                        "created_at": "2026-09-03T00:01:00Z",
                        "content": {"content": "Design this", "type": "text"},
                    }
                ],
            }
        },
    )
    _write_zip(
        directory / "conversations-000.zip",
        {
            "conversations.json": [
                {
                    "uuid": "conversation-1",
                    "name": "Conversation",
                    "summary": "Summary",
                    "created_at": "2026-09-04T00:00:00Z",
                    "updated_at": "2026-09-04T01:00:00Z",
                    "chat_messages": [
                        {
                            "uuid": "message-1",
                            "parent_message_uuid": "parent-1",
                            "sender": "human",
                            "created_at": "2026-09-04T00:01:00Z",
                            "updated_at": "2026-09-04T00:02:00Z",
                            "text": "Different top-level export text",
                            "content": [
                                {"type": "text", "text": "Visible first"},
                                {"type": "tool_use", "name": "ignored-for-text"},
                                {"type": "text", "text": "Visible second"},
                            ],
                            "attachments": [{"file_name": "attached.txt"}],
                            "files": [{"file_name": "referenced.txt"}],
                        }
                    ],
                }
            ]
        },
    )
    manifest = {
        "created_at": "2026-09-09T02:08:46Z",
        "data_files": [
            {
                "category": category,
                "filename": filename,
                "download_url": "https://example.invalid/signed-url-is-not-used",
            }
            for category, filename in archives
        ],
    }
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _all_events(parsed_exports: tuple) -> list:
    return [
        event
        for parsed in parsed_exports
        for conversation in parsed.conversations
        for event in conversation.events
    ] + [event for parsed in parsed_exports for event in parsed.events]


def test_parses_manifest_and_every_supported_archive(tmp_path: Path) -> None:
    manifest = _write_export(tmp_path / "raw-export")

    parsed = parse_anthropic_export(manifest)
    events = _all_events(parsed)

    assert [item.filename for item in parsed] == [
        "light_metadata-000.zip",
        "projects-000.zip",
        "memories-000.zip",
        "design_chats-000.zip",
        "conversations-000.zip",
    ]
    assert all(item.source == "anthropic" for item in parsed)
    assert all(item.parser_version == "anthropic-v1" for item in parsed)
    assert all(item.exported_at.isoformat() == "2026-09-09T02:08:46+00:00" for item in parsed)
    assert sum(item.conversation_count for item in parsed) == 2
    assert sum(item.event_count for item in parsed) == 9
    assert Counter(event.event_type for event in events) == {
        "account_profile_snapshot": 1,
        "account_login": 1,
        "project_snapshot": 1,
        "project_document": 1,
        "memory_snapshot": 2,
        "memory_file_snapshot": 1,
        "conversation_message": 2,
    }
    namespaces = Counter(event.source_record_namespace for event in events)
    assert namespaces["message"] == 1
    assert namespaces["design_message"] == 1
    assert namespaces["project"] == 1


def test_uses_visible_content_blocks_and_preserves_provider_fields(tmp_path: Path) -> None:
    parsed = parse_anthropic_export(_write_export(tmp_path / "raw-export"))
    conversation = next(
        conversation
        for item in parsed
        for conversation in item.conversations
        if conversation.source_conversation_id == "conversation-1"
    )
    event = conversation.events[0]

    assert event.text == "Visible first\nVisible second"
    assert event.source_record_id == "message-1"
    assert event.source_node_id == "message-1"
    assert event.parent_source_node_id == "parent-1"
    assert event.metadata["anthropic"]["top_level_export_text"] == (
        "Different top-level export text"
    )
    assert event.metadata["anthropic"]["attachments"] == [
        {"file_name": "attached.txt"}
    ]
    assert event.metadata["anthropic"]["files"] == [
        {"file_name": "referenced.txt"}
    ]


def test_login_record_ids_are_deterministic(tmp_path: Path) -> None:
    manifest = _write_export(tmp_path / "raw-export")

    first = next(
        event
        for event in _all_events(parse_anthropic_export(manifest))
        if event.event_type == "account_login"
    )
    second = next(
        event
        for event in _all_events(parse_anthropic_export(manifest))
        if event.event_type == "account_login"
    )

    assert first.source_record_id == second.source_record_id
    assert first.source_record_id.startswith("login:")


def test_rejects_manifest_path_traversal(tmp_path: Path) -> None:
    manifest = _write_export(tmp_path / "raw-export")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["data_files"][0]["filename"] = "../light_metadata-000.zip"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AnthropicExportError, match="unsafe filename"):
        parse_anthropic_export(manifest)


def test_accepts_same_ids_in_different_namespaces(tmp_path: Path) -> None:
    manifest = _write_export(tmp_path / "raw-export")
    design_archive = manifest.parent / "design_chats-000.zip"
    _write_zip(
        design_archive,
        {
            "design.json": {
                "uuid": "conversation-1",
                "messages": [
                    {
                        "uuid": "message-1",
                        "role": "user",
                        "content": {"content": "Duplicate ID"},
                    }
                ],
            }
        },
    )

    parsed = parse_anthropic_export(manifest)
    conversations = [
        conversation
        for item in parsed
        for conversation in item.conversations
        if conversation.source_conversation_id == "conversation-1"
    ]
    events = [
        event
        for event in _all_events(parsed)
        if event.source_record_id == "message-1"
    ]

    assert {conversation.source_record_namespace for conversation in conversations} == {
        "chat",
        "design_chat",
    }
    assert {event.source_record_namespace for event in events} == {
        "message",
        "design_message",
    }


def test_rejects_duplicate_event_ids_in_the_same_namespace(tmp_path: Path) -> None:
    manifest = _write_export(tmp_path / "raw-export")
    archive_path = manifest.parent / "conversations-000.zip"
    with ZipFile(archive_path) as archive:
        conversations = json.loads(archive.read("conversations.json"))
    duplicate = dict(conversations[0]["chat_messages"][0])
    duplicate["created_at"] = "2026-09-04T00:03:00Z"
    conversations[0]["chat_messages"].append(duplicate)
    _write_zip(archive_path, {"conversations.json": conversations})

    with pytest.raises(AnthropicExportError, match="duplicate Anthropic record id"):
        parse_anthropic_export(manifest)


def test_rejects_duplicate_conversation_ids_in_the_same_namespace(tmp_path: Path) -> None:
    manifest = _write_export(tmp_path / "raw-export")
    archive_path = manifest.parent / "conversations-000.zip"
    with ZipFile(archive_path) as archive:
        conversations = json.loads(archive.read("conversations.json"))
    duplicate = dict(conversations[0])
    duplicate["chat_messages"] = []
    conversations.append(duplicate)
    _write_zip(archive_path, {"conversations.json": conversations})

    with pytest.raises(AnthropicExportError, match="duplicate Anthropic conversation id"):
        parse_anthropic_export(manifest)


def test_provider_update_time_does_not_change_message_fingerprint(tmp_path: Path) -> None:
    manifest = _write_export(tmp_path / "raw-export")
    first = next(
        event
        for event in _all_events(parse_anthropic_export(manifest))
        if event.source_record_id == "message-1"
    )
    archive_path = manifest.parent / "conversations-000.zip"
    with ZipFile(archive_path) as archive:
        conversations = json.loads(archive.read("conversations.json"))
    conversations[0]["chat_messages"][0]["updated_at"] = "2026-09-05T00:02:00Z"
    _write_zip(archive_path, {"conversations.json": conversations})

    observed_later = next(
        event
        for event in _all_events(parse_anthropic_export(manifest))
        if event.source_record_id == "message-1"
    )

    assert first.provider_updated_at != observed_later.provider_updated_at
    assert "source_updated_at" not in first.metadata["anthropic"]
    assert event_fingerprint(first) == event_fingerprint(observed_later)
    assert event_fingerprint(first.model_copy(update={"text": "actually edited"})) != (
        event_fingerprint(first)
    )
