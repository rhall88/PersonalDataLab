from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from personal_data_lab import cli
from personal_data_lab.models import ImportResult
from personal_data_lab.openai_export import orchestrator
from personal_data_lab.openai_export.inventory import (
    OpenAIExportReader,
    OpenAIInventoryError,
    inspect_openai,
)
from personal_data_lab.openai_export.parser import (
    ASSET_NAMESPACE,
    MESSAGE_NAMESPACE,
    iter_conversation_member_exports,
    load_asset_catalog,
    parse_asset_member,
    parse_structured_member,
)


def _conversation(conversation_id: str, *, provider_message_id: str = "shared-message") -> dict:
    return {
        "id": conversation_id,
        "title": f"Title {conversation_id}",
        "create_time": 1_700_000_000,
        "update_time": 1_700_000_010,
        "mapping": {
            "root": {"id": "root", "parent": None, "message": None},
            "node-user": {
                "id": "node-user",
                "parent": "root",
                "message": {
                    "id": provider_message_id,
                    "author": {"role": "user"},
                    "create_time": 1_700_000_001,
                    "content": {
                        "content_type": "multimodal_text",
                        "parts": [
                            "Hello",
                            {"asset_pointer": "sediment://file_present"},
                        ],
                    },
                    "metadata": {},
                },
            },
            "node-assistant": {
                "id": "node-assistant",
                "parent": "node-user",
                "message": {
                    "id": f"assistant-{conversation_id}",
                    "author": {"role": "assistant"},
                    "create_time": 1_700_000_002,
                    "content": {"content_type": "text", "parts": ["Hi"]},
                    "metadata": {"attachments": [{"asset_pointer": "sediment://file_missing"}]},
                },
            },
        },
    }


def _export_members() -> dict[str, bytes]:
    members = {
        "conversations-000.json": json.dumps([_conversation("conversation-a")]).encode(),
        "conversations-001.json": json.dumps([_conversation("conversation-b")]).encode(),
        "conversation_asset_file_names.json": json.dumps(
            {"file_present.dat": "photo.png", "file_missing.dat": "missing.pdf"}
        ).encode(),
        "library_files.json": json.dumps(
            [
                {
                    "file_id": "file_present",
                    "file_name": "library-photo.png",
                    "mime_type": "image/png",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-02T00:00:00Z",
                    "state": "ready",
                }
            ]
        ).encode(),
        "file_present.dat": b"PNG BYTES",
        "chat.html": b"<html></html>",
        "sites/worker.bundle": b"bundle",
        "ads.json": b"{}",
    }
    return members


def _write_export(path: Path, *, mutate_manifest=None, mutate_members=None) -> None:
    members = _export_members()
    if mutate_members is not None:
        mutate_members(members)
    logical = {
        "conversations.json": {
            "files": ["conversations-000.json", "conversations-001.json"],
            "sharded": True,
        }
    }
    for name in members:
        if name.startswith("conversations-"):
            continue
        logical[name] = {"files": [name], "sharded": False}
    manifest = {
        "version": 1,
        "manifest_file": "export_manifest.json",
        "export_files": [{"path": name, "size_bytes": len(raw)} for name, raw in members.items()],
        "logical_files": logical,
    }
    if mutate_manifest is not None:
        mutate_manifest(manifest)
    with ZipFile(path, "w") as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)
        archive.writestr("export_manifest.json", json.dumps(manifest))


def test_inventory_reconciles_physical_and_logical_manifest(tmp_path: Path) -> None:
    source = tmp_path / "openai.zip"
    _write_export(source)

    inventory = inspect_openai(source)

    assert inventory.physical_members_total == 9
    assert inventory.logical_files_total == 7
    assert inventory.conversation_shards == 2
    assert inventory.structured_json_files == 3
    assert inventory.binary_asset_files == 2
    assert inventory.rendered_html_files == 1
    assert inventory.unknown_files == 0
    assert inventory.complete_accounting
    public = inventory.public_dict()
    assert "source_path" not in public
    assert all("member_path" not in item for item in public["members"])


def test_inspect_cli_is_private_by_default_and_supports_table(tmp_path: Path, capsys) -> None:
    source = tmp_path / "private-openai-export.zip"
    _write_export(source)

    assert cli.main(["inspect-openai", str(source)]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["physical_members_total"] == 9
    assert "private-openai-export" not in captured.out
    assert "conversations-000.json" not in captured.out

    assert cli.main(["inspect-openai", str(source), "--format", "table"]) == 0
    captured = capsys.readouterr()
    assert "physical members: 9" in captured.out
    assert "unknown files: 0" in captured.out
    assert "private-openai-export" not in captured.out


def test_inventory_rejects_manifest_size_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "openai.zip"

    def mutate(manifest: dict) -> None:
        manifest["export_files"][0]["size_bytes"] += 1

    _write_export(source, mutate_manifest=mutate)

    with pytest.raises(OpenAIInventoryError, match="size mismatch"):
        inspect_openai(source)


def test_conversation_parser_scopes_message_identity_and_preserves_assets(
    tmp_path: Path,
) -> None:
    source = tmp_path / "openai.zip"
    _write_export(source)
    inventory = inspect_openai(source)

    with OpenAIExportReader(source) as reader:
        catalog = load_asset_catalog(reader, inventory.members)
        members = [
            item for item in inventory.members if item.accounting_category == "conversation_shard"
        ]
        seen: set[str] = set()
        parsed = [
            export
            for member in members
            for export in iter_conversation_member_exports(
                reader,
                member,
                catalog,
                seen_conversation_ids=seen,
            )
        ]

    assert len(parsed) == 2
    events = [
        event
        for item in parsed
        for conversation in item.conversations
        for event in conversation.events
    ]
    assert len(events) == 4
    assert len({event.source_record_id for event in events}) == 4
    assert {event.metadata["openai"]["provider_message_id"] for event in events}.issuperset(
        {"shared-message"}
    )
    assert all(event.source_record_namespace == MESSAGE_NAMESPACE for event in events)
    assert {event.text for event in events} == {"Hello", "Hi"}
    relationships = [item for export in parsed for item in export.relationships]
    assert sum(item.relationship_type == "message_has_parent" for item in relationships) == 2
    assert sum(item.relationship_type == "message_has_asset" for item in relationships) == 4
    assert (
        sum(
            item.metadata["openai"]["available_in_export"]
            for item in relationships
            if item.relationship_type == "message_has_asset"
        )
        == 2
    )
    placeholders = [asset for export in parsed for asset in export.assets]
    assert {asset.source_asset_id for asset in placeholders} == {"file_missing"}
    assert all(asset.sha256 is None and asset.size_bytes == 0 for asset in placeholders)


def test_assets_and_library_records_link_without_storing_binary_payload(tmp_path: Path) -> None:
    source = tmp_path / "openai.zip"
    _write_export(source)
    inventory = inspect_openai(source)

    with OpenAIExportReader(source) as reader:
        catalog = load_asset_catalog(reader, inventory.members)
        asset_member = next(
            item for item in inventory.members if item.filename == "file_present.dat"
        )
        asset_export = parse_asset_member(reader, asset_member, catalog)
        library_member = next(
            item for item in inventory.members if item.filename == "library_files.json"
        )
        library_export = parse_structured_member(reader, library_member, catalog)

    asset = asset_export.assets[0]
    assert asset.asset_namespace == ASSET_NAMESPACE
    assert asset.source_asset_id == "file_present"
    assert asset.filename == "photo.png"
    assert asset.mime_type == "image/png"
    assert asset.sha256 is not None
    assert asset.size_bytes == len(b"PNG BYTES")
    assert library_export.record_count == 1
    assert library_export.relationship_count == 1
    relationship = library_export.relationships[0]
    assert relationship.relationship_type == "library_file_has_export_asset"
    assert relationship.object_source_id == "file_present"


def test_parser_escapes_nul_without_losing_its_position(tmp_path: Path) -> None:
    source = tmp_path / "openai.zip"

    def mutate(members: dict[str, bytes]) -> None:
        conversations = json.loads(members["conversations-000.json"])
        conversations[0]["mapping"]["node-user"]["message"]["content"]["parts"][0] = (
            "before\x00after"
        )
        members["conversations-000.json"] = json.dumps(conversations).encode()

    _write_export(source, mutate_members=mutate)
    inventory = inspect_openai(source)

    with OpenAIExportReader(source) as reader:
        catalog = load_asset_catalog(reader, inventory.members)
        member = next(
            item for item in inventory.members if item.filename == "conversations-000.json"
        )
        parsed = next(
            iter_conversation_member_exports(
                reader,
                member,
                catalog,
                seen_conversation_ids=set(),
            )
        )

    event = parsed.conversations[0].events[0]
    assert event.text == "before\\u0000after"
    assert "\x00" not in json.dumps(event.metadata)
    assert "\\\\u0000" in json.dumps(event.metadata)


def test_orchestrator_reports_complete_restart_safe_accounting(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "openai.zip"
    _write_export(source)
    next_id = 0

    def fake_import(_engine, batch):
        nonlocal next_id
        results = []
        for parsed in batch:
            next_id += 1
            results.append(
                ImportResult(
                    source_file_id=next_id,
                    run_id=next_id,
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
                )
            )
        return tuple(results)

    monkeypatch.setattr(orchestrator, "import_parsed_exports", fake_import)

    summary = orchestrator.import_openai_all(object(), str(source))

    assert summary.files_processed == summary.physical_members_total == 9
    assert summary.files_failed == 0
    assert summary.chunks == 9
    assert summary.conversations == 2
    assert summary.messages == 4
    assert summary.records == 4
    assert summary.exported_assets == 3
    assert summary.unavailable_assets == 1
    assert summary.asset_references == 4
    assert summary.resolved_asset_references == 2
    assert summary.unavailable_asset_references == 2
    assert summary.parent_relationships == 2
    assert summary.relationships == 7
    assert summary.complete_accounting
    assert summary.production_ready
    assert summary.exit_code == 0
