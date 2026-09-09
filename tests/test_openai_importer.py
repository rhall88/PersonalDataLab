from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from personal_data_lab.openai_importer import OpenAIExportError, parse_openai_export


def _conversation() -> dict:
    return {
        "id": "conversation-1",
        "title": "First test",
        "create_time": 1_700_000_000,
        "update_time": 1_700_000_010,
        "mapping": {
            "root": {"id": "root", "parent": None, "message": None},
            "node-user": {
                "id": "node-user",
                "parent": "root",
                "message": {
                    "id": "message-user",
                    "author": {"role": "user"},
                    "create_time": 1_700_000_001,
                    "content": {"content_type": "text", "parts": ["Hello"]},
                },
            },
            "node-assistant": {
                "id": "node-assistant",
                "parent": "node-user",
                "message": {
                    "id": "message-assistant",
                    "author": {"role": "assistant"},
                    "create_time": 1_700_000_002,
                    "content": {
                        "content_type": "multimodal_text",
                        "parts": ["Hi", {"text": "there"}],
                    },
                },
            },
        },
    }


def test_parses_standalone_conversations_json(tmp_path: Path) -> None:
    source = tmp_path / "conversations.json"
    source.write_text(json.dumps([_conversation()]), encoding="utf-8")

    parsed = parse_openai_export(source)

    assert parsed.conversation_count == 1
    assert parsed.event_count == 2
    assert parsed.conversations[0].events[0].text == "Hello"
    assert parsed.conversations[0].events[1].text == "Hi\nthere"
    assert parsed.conversations[0].events[1].actor_identity == "assistant"
    assert parsed.conversations[0].events[0].source_record_id == "message-user"
    assert parsed.conversations[0].events[0].source_node_id == "node-user"
    assert parsed.conversations[0].events[1].parent_source_node_id == "node-user"
    assert (
        parsed.conversations[0].events[1].parent_source_node_id
        == parsed.conversations[0].events[0].source_node_id
    )
    assert "parent_source_record_id" not in parsed.conversations[0].events[1].metadata
    assert len(parsed.sha256) == 64


def test_parses_zip_without_extracting_it(tmp_path: Path) -> None:
    source = tmp_path / "openai-export.zip"
    with ZipFile(source, "w") as archive:
        archive.writestr("export/conversations.json", json.dumps([_conversation()]))

    parsed = parse_openai_export(source)

    assert parsed.filename == "openai-export.zip"
    assert parsed.event_count == 2
    assert parsed.original_path == str(source.resolve())


def test_directory_import_records_the_actual_json_path(tmp_path: Path) -> None:
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    source = extracted / "conversations.json"
    source.write_text(json.dumps([_conversation()]), encoding="utf-8")

    parsed = parse_openai_export(extracted)

    assert parsed.filename == "conversations.json"
    assert parsed.original_path == str(source.resolve())


def test_preserves_non_text_messages_for_reconciliation(tmp_path: Path) -> None:
    conversation = _conversation()
    conversation["mapping"]["node-user"]["message"]["content"] = {
        "content_type": "computer_output",
        "parts": [{"asset_pointer": "sediment://asset"}],
    }
    source = tmp_path / "conversations.json"
    source.write_text(json.dumps([conversation]), encoding="utf-8")

    parsed = parse_openai_export(source)

    assert parsed.event_count == 2
    assert parsed.conversations[0].events[0].text is None


def test_rejects_missing_conversations_json(tmp_path: Path) -> None:
    with pytest.raises(OpenAIExportError, match="no conversations.json"):
        parse_openai_export(tmp_path)


def test_rejects_conversation_without_id(tmp_path: Path) -> None:
    conversation = _conversation()
    conversation.pop("id")
    source = tmp_path / "conversations.json"
    source.write_text(json.dumps([conversation]), encoding="utf-8")

    with pytest.raises(OpenAIExportError, match="has no id"):
        parse_openai_export(source)


def test_uses_mapping_node_id_when_message_id_is_absent(tmp_path: Path) -> None:
    conversation = _conversation()
    conversation["mapping"]["node-user"]["message"].pop("id")
    source = tmp_path / "conversations.json"
    source.write_text(json.dumps([conversation]), encoding="utf-8")

    parsed = parse_openai_export(source)
    event = parsed.conversations[0].events[0]

    assert event.source_record_id == "node-user"
    assert event.source_node_id == "node-user"
    assert event.metadata["source_record_id_fallback"] == "mapping_node_id"


def test_rejects_duplicate_message_ids_within_export(tmp_path: Path) -> None:
    conversation = _conversation()
    conversation["mapping"]["node-assistant"]["message"]["id"] = "message-user"
    source = tmp_path / "conversations.json"
    source.write_text(json.dumps([conversation]), encoding="utf-8")

    with pytest.raises(
        OpenAIExportError,
        match="duplicate OpenAI message id in export: message-user",
    ):
        parse_openai_export(source)
