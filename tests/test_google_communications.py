from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from personal_data_lab.google_takeout import communications, orchestrator
from personal_data_lab.google_takeout.adapters import (
    DEFAULT_ADAPTER_REGISTRY,
    AdapterRegistry,
    open_takeout_member,
)
from personal_data_lab.google_takeout.inventory import inspect_google
from personal_data_lab.models import ImportResult


def _zip(path: Path, members: dict[str, str | bytes]) -> Path:
    with ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


def _member(path: Path, suffix: str):
    return next(
        item for item in inspect_google(path).members if item.member_path.endswith(suffix)
    )


def _parse(path: Path, suffix: str):
    member = _member(path, suffix)
    adapter = DEFAULT_ADAPTER_REGISTRY.resolve(member)
    assert adapter is not None
    with open_takeout_member(member) as stream:
        return adapter, adapter.parse(stream, member)


def _mbox_message(message_id: str, *, attachment: bool = False) -> str:
    if not attachment:
        return (
            "From sender@example.invalid Tue Sep 16 12:00:00 2026\n"
            f"Message-ID: <{message_id}>\n"
            "X-GM-THRID: thread-1\n"
            "Date: Tue, 16 Sep 2026 12:00:00 +0000\n"
            "From: sender@example.invalid\n"
            "To: owner@example.invalid\n"
            "Subject: Synthetic message\n"
            "Content-Type: text/plain; charset=utf-8\n"
            "\n"
            "Synthetic body\n"
        )
    return (
        "From sender@example.invalid Tue Sep 16 12:00:00 2026\n"
        f"Message-ID: <{message_id}>\n"
        "X-GM-THRID: thread-1\n"
        "Date: Tue, 16 Sep 2026 12:00:00 +0000\n"
        "From: sender@example.invalid\n"
        "To: owner@example.invalid\n"
        "Subject: Synthetic attachment\n"
        "MIME-Version: 1.0\n"
        "Content-Type: multipart/mixed; boundary=BOUNDARY\n"
        "\n"
        "--BOUNDARY\n"
        "Content-Type: text/plain; charset=utf-8\n"
        "\n"
        "Synthetic body\n"
        "--BOUNDARY\n"
        "Content-Type: application/octet-stream\n"
        "Content-Disposition: attachment; filename=synthetic.bin\n"
        "Content-Transfer-Encoding: base64\n"
        "\n"
        "c3ludGhldGljLWJ5dGVz\n"
        "--BOUNDARY--\n"
    )


def test_mbox_is_two_pass_batched_and_preserves_messages_attachments_and_threads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(communications, "MBOX_BATCH_MESSAGES", 2)
    content = (
        _mbox_message("message-1@example.invalid")
        + _mbox_message("message-2@example.invalid", attachment=True)
        + _mbox_message("message-3@example.invalid")
    ).encode()
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {"Takeout/Mail/All mail Including Spam and Trash.mbox": content},
    )
    member = _member(source, ".mbox")
    adapter = DEFAULT_ADAPTER_REGISTRY.resolve(member)

    assert adapter is not None
    assert adapter.adapter_name == "gmail_mbox"
    chunks = list(adapter.iter_parsed_exports(member))

    assert len(chunks) == 2
    assert {chunk.sha256 for chunk in chunks} == {hashlib.sha256(content).hexdigest()}
    assert all(chunk.size_bytes == len(content) for chunk in chunks)
    assert sum(chunk.event_count for chunk in chunks) == 3
    assert sum(chunk.asset_count for chunk in chunks) == 1
    assert sum(chunk.relationship_count for chunk in chunks) == 4
    assert {record.source_record_id for chunk in chunks for record in chunk.records} == {
        "provider:thread-1"
    }
    assert chunks[0].assets[0].sha256 == hashlib.sha256(b"synthetic-bytes").hexdigest()


def test_mbox_oversized_message_is_bounded_and_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(communications, "MAX_MBOX_MESSAGE_BYTES", 64)
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Mail/All mail.mbox": _mbox_message(
                "oversized@example.invalid"
            )
        },
    )
    member = _member(source, ".mbox")
    adapter = DEFAULT_ADAPTER_REGISTRY.resolve(member)
    assert adapter is not None

    chunks = list(adapter.iter_parsed_exports(member))

    assert len(chunks) == 1
    assert chunks[0].events[0].event_type == "mail_message_oversized"
    assert chunks[0].parse_warnings == ("MBOX message exceeded bounded parser limit",)


def test_google_chat_messages_and_explicit_attachment_references(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Google Chat/Groups/Space/messages.json": json.dumps(
                {
                    "messages": [
                        {
                            "message_id": "message-1",
                            "topic_id": "topic-1",
                            "created_date": "2026-09-16T12:00:00Z",
                            "creator": {
                                "email": "person@example.invalid",
                                "name": "Synthetic Person",
                                "user_type": "Human",
                            },
                            "text": "Synthetic message",
                            "attached_files": [
                                {
                                    "name": "synthetic.jpg",
                                    "mime_type": "image/jpeg",
                                    "future": "preserved",
                                }
                            ],
                        }
                    ]
                }
            )
        },
    )

    adapter, parsed = _parse(source, "messages.json")

    assert adapter.adapter_name == "communications_semantic"
    assert parsed.record_count == parsed.event_count == parsed.asset_count == 1
    assert parsed.relationship_count == 2
    assert parsed.events[0].event_type == "chat_message"
    assert parsed.assets[0].metadata["google"]["future"] == "preserved"


def test_meet_groups_voice_and_binary_assets_are_semantic(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Google Meet/Calls.csv": (
                "Item Id,Start Time,End Time,Call Direction\r\n"
                "call-1,2026-09-16T12:00:00Z,2026-09-16T12:30:00Z,Outgoing\r\n"
            ),
            "Takeout/Groups/subscriptions.csv": (
                "Group Email,Search Query\r\n"
                "group@example.invalid,label:synthetic\r\n"
            ),
            "Takeout/Voice/call.html": (
                "<html><body><abbr title='2026-09-16T12:00:00Z'>time</abbr>"
                "<div>Synthetic call</div></body></html>"
            ),
            "Takeout/Voice/audio.mp3": b"ID3-synthetic-audio",
        },
    )

    meet_adapter, meet = _parse(source, "Calls.csv")
    groups_adapter, groups = _parse(source, "subscriptions.csv")
    voice_adapter, voice = _parse(source, "call.html")
    asset_adapter, asset = _parse(source, "audio.mp3")

    assert meet_adapter.adapter_name == groups_adapter.adapter_name == "communications_semantic"
    assert voice_adapter.adapter_name == "communications_semantic"
    assert asset_adapter.adapter_name == "communications_asset"
    assert meet.events[0].event_type == "meet_call"
    assert groups.records[0].record_type == "group_membership"
    assert voice.events[0].event_type == "voice_communication"
    assert asset.assets[0].mime_type == "audio/mpeg"
    assert asset.assets[0].sha256 == hashlib.sha256(b"ID3-synthetic-audio").hexdigest()


def test_orchestrator_counts_one_streaming_member_and_all_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(communications, "MBOX_BATCH_MESSAGES", 1)
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Mail/All mail.mbox": (
                _mbox_message("message-1@example.invalid")
                + _mbox_message("message-2@example.invalid")
            )
        },
    )
    imported_chunks = []

    def imported(engine, batch):
        parsed = batch[0]
        imported_chunks.append(parsed)
        return (
            ImportResult(
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
                inserted_events=parsed.event_count,
                inserted_records=parsed.record_count,
                inserted_assets=parsed.asset_count,
                inserted_relationships=parsed.relationship_count,
            ),
        )

    monkeypatch.setattr(orchestrator, "import_parsed_exports", imported)
    registry = AdapterRegistry((communications.MboxAdapter(),))

    summary = orchestrator.import_google_all(object(), str(source), registry=registry)

    assert len(imported_chunks) == 2
    assert summary.members_imported == 1
    assert summary.coverage["mail"].members_processed == 1
    assert summary.coverage["mail"].events_parsed == 2
    assert summary.coverage["mail"].bytes_processed == len(
        (_mbox_message("message-1@example.invalid") + _mbox_message("message-2@example.invalid")).encode()
    )
