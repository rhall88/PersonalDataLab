from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from personal_data_lab import cli
from personal_data_lab.anthropic_export.inventory import (
    AnthropicInventoryError,
    inspect_anthropic,
)
from personal_data_lab.anthropic_export.orchestrator import import_anthropic_all
from personal_data_lab.anthropic_export.parser import (
    PARSER_VERSION,
    AnthropicAdapterError,
    iter_archive_exports,
    parse_manifest_export,
)
from personal_data_lab.models import ImportResult


def _write_zip(path: Path, entries: dict[str, object | bytes]) -> None:
    with ZipFile(path, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(
                name,
                payload if isinstance(payload, bytes) else json.dumps(payload),
            )


def _write_export(directory: Path) -> Path:
    directory.mkdir()
    _write_zip(
        directory / "light_metadata-000.zip",
        {
            "users.json": [{"uuid": "account-1", "full_name": "Private Person"}],
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
            "projects/private-project.json": {
                "uuid": "project-1",
                "name": "Private Project",
                "description": "Private description",
                "created_at": "2026-08-01T00:00:00Z",
                "updated_at": "2026-09-01T00:00:00Z",
                "creator": {"uuid": "account-1"},
                "docs": [
                    {
                        "uuid": "document-1",
                        "filename": "private-notes.txt",
                        "content": "Private project notes",
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
                "conversations_memory": "Private general memory",
                "project_memories": {"project-1": "Private project memory"},
                "memory_files": [{"path": "memory/private.txt", "content": "Private memory file"}],
            }
        },
    )
    _write_zip(
        directory / "design_chats-000.zip",
        {
            "design_chats/private-design.json": {
                "uuid": "design-1",
                "title": "Private design",
                "project": {"uuid": "project-1", "name": "Private Project"},
                "messages": [
                    {
                        "uuid": "design-message-1",
                        "role": "user",
                        "content": {"content": "Private design request", "type": "text"},
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
                    "name": "Private conversation",
                    "updated_at": "2026-09-04T01:00:00Z",
                    "chat_messages": [
                        {
                            "uuid": "message-1",
                            "sender": "human",
                            "content": [{"type": "text", "text": "Private question"}],
                            "attachments": [
                                {
                                    "path": "exports/files/available.bin",
                                    "file_name": "private-available.bin",
                                }
                            ],
                        },
                        {
                            "uuid": "message-2",
                            "parent_message_uuid": "message-1",
                            "sender": "assistant",
                            "content": [{"type": "text", "text": "Private answer"}],
                            "files": [
                                {"uuid": "missing-file-1", "file_name": "private-missing.txt"}
                            ],
                        },
                    ],
                },
                {
                    "uuid": "conversation-2",
                    "name": "Second private conversation",
                    "chat_messages": [],
                },
            ],
            "exports/files/available.bin": b"private binary bytes",
        },
    )
    manifest = {
        "uuid": "export-1",
        "created_at": "2026-09-09T02:08:46Z",
        "data_files": [
            {
                "category": category,
                "filename": f"{category}-000.zip",
                "download_url": "https://example.invalid/private-signed-url",
            }
            for category in (
                "light_metadata",
                "projects",
                "memories",
                "design_chats",
                "conversations",
            )
        ],
    }
    path = directory / "private-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _result(parsed, *, short_circuited: bool = False) -> ImportResult:
    return ImportResult(
        source_file_id=1,
        source_conversations=parsed.conversation_count,
        database_conversations=parsed.conversation_count,
        source_events=parsed.event_count,
        database_events=parsed.event_count,
        source_records=parsed.record_count,
        database_records=parsed.record_count,
        source_assets=parsed.asset_count,
        database_assets=parsed.asset_count,
        source_relationships=parsed.relationship_count,
        database_relationships=parsed.relationship_count,
        short_circuited=short_circuited,
    )


def test_inventory_accounts_for_manifest_archives_members_and_bytes(tmp_path: Path) -> None:
    manifest = _write_export(tmp_path / "private-export")

    inventory = inspect_anthropic(manifest)

    assert inventory.complete_accounting
    assert inventory.source_files_total == 6
    assert inventory.declared_archives == 5
    assert inventory.undeclared_archives == 0
    assert inventory.archive_members_total == 7
    assert inventory.structured_json_members == 6
    assert inventory.binary_asset_members == 1
    assert inventory.physical_source_bytes == manifest.stat().st_size + sum(
        item.size_bytes for item in inventory.archives
    )
    redacted = json.dumps(inventory.public_dict())
    assert "private-manifest.json" not in redacted
    assert "private-project.json" not in redacted
    assert "private-available.bin" not in redacted
    disclosed = json.dumps(inventory.public_dict(include_paths=True))
    assert "private-manifest.json" in disclosed
    assert "private-project.json" in disclosed


def test_inventory_rejects_unsafe_members_and_flags_undeclared_archives(
    tmp_path: Path,
) -> None:
    manifest = _write_export(tmp_path / "private-export")
    _write_zip(manifest.parent / "undeclared.zip", {"other.json": {"private": True}})
    inventory = inspect_anthropic(manifest)
    assert inventory.undeclared_archives == 1
    assert inventory.unknown_files == 1

    _write_zip(manifest.parent / "projects-000.zip", {"../private.json": {"uuid": "p"}})
    with pytest.raises(AnthropicInventoryError, match="unsafe member path"):
        inspect_anthropic(manifest)


def test_parser_rejects_sources_changed_after_inventory(tmp_path: Path) -> None:
    manifest = _write_export(tmp_path / "private-export")
    inventory = inspect_anthropic(manifest)
    manifest.write_text(manifest.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(AnthropicAdapterError, match="manifest changed"):
        parse_manifest_export(inventory)

    inventory = inspect_anthropic(manifest)
    archive = next(item for item in inventory.archives if item.category == "projects")
    with Path(archive.source_path).open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises(AnthropicAdapterError, match="archive changed"):
        next(
            iter_archive_exports(
                inventory,
                archive,
                seen_conversations=set(),
                seen_events=set(),
            )
        )


def test_v2_parser_chunks_conversations_and_builds_evidence_graph(tmp_path: Path) -> None:
    inventory = inspect_anthropic(_write_export(tmp_path / "private-export"))
    conversation_archive = next(
        item for item in inventory.archives if item.category == "conversations"
    )

    parsed = list(
        iter_archive_exports(
            inventory,
            conversation_archive,
            seen_conversations=set(),
            seen_events=set(),
            conversations_per_chunk=1,
        )
    )

    assert len(parsed) == 3
    assert all(item.parser_version == PARSER_VERSION for _, item in parsed)
    assert sum(item.conversation_count for _, item in parsed) == 2
    assert sum(item.event_count for _, item in parsed) == 2
    assets = [asset for _, item in parsed for asset in item.assets]
    relationships = [relationship for _, item in parsed for relationship in item.relationships]
    assert {asset.metadata["anthropic"]["available_in_export"] for asset in assets} == {
        True,
        False,
    }
    assert {item.relationship_type for item in relationships} == {
        "message_has_parent",
        "message_has_attachment",
        "message_has_file",
    }


def test_orchestrator_reports_complete_restart_safe_accounting(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = _write_export(tmp_path / "private-export")
    imported_categories: list[str | None] = []

    def fake_import(engine, batch):
        parsed = batch[0]
        imported_categories.append(parsed.category)
        return (_result(parsed, short_circuited=True),)

    monkeypatch.setattr(
        "personal_data_lab.anthropic_export.orchestrator.import_parsed_exports",
        fake_import,
    )
    summary = import_anthropic_all(object(), str(manifest))

    assert summary.complete_accounting
    assert summary.production_ready
    assert summary.exit_code == 0
    assert summary.files_processed == 6
    assert summary.files_short_circuited == 6
    assert summary.files_failed == 0
    assert summary.members_processed == 7
    assert summary.members_failed == 0
    assert summary.conversations == 3
    assert summary.events == 10
    assert summary.records == 1
    assert summary.assets == 2
    assert summary.available_assets == 1
    assert summary.unavailable_assets == 1
    assert summary.relationships == 5
    assert summary.parent_relationships == 1
    assert summary.attachment_relationships == 2
    assert imported_categories[0] == "anthropic/manifest"


def test_cli_inventory_is_private_and_import_uses_opaque_progress(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    manifest = _write_export(tmp_path / "private-export")
    assert cli.main(["inspect-anthropic", str(manifest)]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["archive_members_total"] == 7
    assert "private" not in captured.out.casefold()

    monkeypatch.setattr(cli, "database_url", lambda override: "ignored")
    monkeypatch.setattr(cli, "create_database_engine", lambda url: object())

    def fake_import(engine, batch):
        return (_result(batch[0]),)

    monkeypatch.setattr(
        "personal_data_lab.anthropic_export.orchestrator.import_parsed_exports",
        fake_import,
    )
    assert cli.main(["import-anthropic", str(manifest)]) == 0
    captured = capsys.readouterr()
    assert "private" not in captured.err.casefold()
    assert "private" not in captured.out.casefold()
    assert "source_id=anthropic-" in captured.err
