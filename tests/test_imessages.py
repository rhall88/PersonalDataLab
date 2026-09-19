from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from personal_data_lab import cli
from personal_data_lab.imessages import orchestrator
from personal_data_lab.imessages.inventory import EXPECTED_HEADERS, inspect_imessages
from personal_data_lab.imessages.parser import IMessageAdapterError, iter_imessage_exports
from personal_data_lab.models import ImportResult


def _write_csv(
    path: Path,
    rows: list[dict[str, str]],
    *,
    delimiter: str = ",",
) -> Path:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPECTED_HEADERS, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _row(**updates: str) -> dict[str, str]:
    result = dict.fromkeys(EXPECTED_HEADERS, "")
    result.update(
        {
            "Chat Session": "Example Chat",
            "Message Date": "2026-09-01 12:00:00",
            "Service": "iMessage",
            "Type": "Outgoing",
            "Sender ID": "me@example.test",
            "Sender Name": "Me",
            "Status": "Read",
            "Text": "hello",
        }
    )
    result.update(updates)
    return result


def _parsed(source: Path, *, batch_rows: int = 2):
    inventory = inspect_imessages(source)
    member = next(item for item in inventory.files if item.accounting_category == "classified_csv")
    return tuple(
        iter_imessage_exports(
            member,
            inventory,
            timezone_name="America/Indianapolis",
            device_label="test-device",
            batch_rows=batch_rows,
        )
    )


def test_inventory_accounts_for_rows_semantics_and_private_paths(tmp_path: Path) -> None:
    secret = "PRIVATE CHAT"
    _write_csv(
        tmp_path / f"{secret}.csv",
        [
            _row(
                **{
                    "Chat Session": secret,
                    "Edited Date": "2026-09-01 12:01:00",
                    "Replying to": "↩ Replying to you, 2026-09-01 11:00:00: «older»",
                    "Reactions": "You reacted with ❤️ on 9/1/2026 12:02:00 PM",
                    "Attachment": "private.jpg",
                }
            )
        ],
    )
    inventory = inspect_imessages(tmp_path)

    assert inventory.complete_accounting
    assert inventory.csv_files == inventory.records == inventory.chat_sessions == 1
    assert inventory.replies == inventory.reactions == inventory.edits == 1
    assert inventory.attachment_references == 1
    assert secret not in json.dumps(inventory.public_dict())
    assert secret in json.dumps(inventory.public_dict(include_paths=True))


@pytest.mark.parametrize("delimiter", [";", "\t"])
def test_inventory_supports_documented_imazing_delimiters(tmp_path: Path, delimiter: str) -> None:
    _write_csv(tmp_path / "messages.csv", [_row()], delimiter=delimiter)
    inventory = inspect_imessages(tmp_path)
    assert inventory.csv_files == 1
    assert inventory.unknown_files == 0


def test_parser_normalizes_messages_replies_reactions_lifecycle_and_assets(
    tmp_path: Path,
) -> None:
    (tmp_path / "photo.jpg").write_bytes(b"image bytes")
    _write_csv(
        tmp_path / "messages.csv",
        [
            _row(),
            _row(
                **{
                    "Message Date": "2026-09-01 12:03:00",
                    "Type": "Incoming",
                    "Sender ID": "+15555550100",
                    "Sender Name": "Ada",
                    "Text": "reply",
                    "Replying to": "↩ Replying to you, 2026-09-01 12:00:00: «hello»",
                    "Reactions": "Ada reacted with ❤️ on 9/1/2026 12:04:00 PM",
                    "Edited Date": "2026-09-01 12:05:00",
                    "Attachment": "photo.jpg",
                    "Attachment type": "Image",
                }
            ),
            _row(
                **{
                    "Message Date": "2026-09-01 12:06:00",
                    "Deleted Date": "2026-09-01 12:07:00",
                    "Status": "Recently deleted",
                    "Text": "deleted but preserved",
                }
            ),
        ],
    )
    parsed = _parsed(tmp_path, batch_rows=10)[0]
    events = [event for conversation in parsed.conversations for event in conversation.events]
    relationships = {item.relationship_type: item for item in parsed.relationships}

    assert sum(event.source_record_namespace == "imazing_csv/message" for event in events) == 3
    assert any(event.event_type == "message.reaction" for event in events)
    assert any(event.event_type == "message.edit" for event in events)
    assert any(event.event_type == "message.deletion" for event in events)
    assert any(event.lifecycle_state == "provider_deleted" for event in events)
    assert relationships["message_replies_to"].object_kind == "event"
    assert parsed.assets[0].sha256 is not None
    assert parsed.assets[0].metadata["imazing"]["available_locally"]
    assert any(record.record_type == "message_participant" for record in parsed.records)


def test_removed_reaction_is_a_structured_lifecycle_event(tmp_path: Path) -> None:
    _write_csv(
        tmp_path / "messages.csv",
        [_row(Reactions="Me removed the reaction ❤️ on 9/1/2026 12:04:00 PM")],
    )
    parsed = _parsed(tmp_path, batch_rows=10)[0]
    reaction = next(
        event
        for conversation in parsed.conversations
        for event in conversation.events
        if event.event_type == "message.reaction_removed"
    )
    assert reaction.metadata["imazing"]["parsed"]
    assert reaction.metadata["imazing"]["action"] == "removed"


def test_exact_duplicate_rows_receive_distinct_stable_ids_across_chunks(tmp_path: Path) -> None:
    row = _row()
    _write_csv(tmp_path / "messages.csv", [row, row])
    first = _parsed(tmp_path, batch_rows=1)
    second = _parsed(tmp_path, batch_rows=2)

    def ids(exports) -> list[str]:
        return [
            event.source_record_id
            for parsed in exports
            for conversation in parsed.conversations
            for event in conversation.events
            if event.source_record_namespace == "imazing_csv/message"
        ]

    assert len(set(ids(first))) == 2
    assert ids(first) == ids(second)


def test_ambiguous_reply_is_preserved_without_guessing(tmp_path: Path) -> None:
    same_time = "2026-09-01 12:00:00"
    _write_csv(
        tmp_path / "messages.csv",
        [
            _row(**{"Message Date": same_time, "Text": "same"}),
            _row(**{"Message Date": same_time, "Text": "same"}),
            _row(
                **{
                    "Message Date": "2026-09-01 12:01:00",
                    "Replying to": f"↩ Replying to you, {same_time}: «same»",
                }
            ),
        ],
    )
    parsed = _parsed(tmp_path, batch_rows=10)[0]
    reply = next(
        item for item in parsed.relationships if item.relationship_type == "message_replies_to"
    )
    reference = next(
        item for item in parsed.records if item.record_type == "message_reply_reference"
    )
    assert reply.object_kind == "record"
    assert reply.object_source_id == reference.source_record_id
    assert reply.metadata["imazing"]["resolution"] == "unresolved"


def test_timezone_is_explicit_and_errors_do_not_echo_payload(tmp_path: Path) -> None:
    secret = "DO NOT LOG THIS MESSAGE"
    _write_csv(tmp_path / "messages.csv", [_row(**{"Message Date": secret})])
    inventory = inspect_imessages(tmp_path)
    member = inventory.files[0]
    with pytest.raises(IMessageAdapterError) as raised:
        tuple(
            iter_imessage_exports(
                member,
                inventory,
                timezone_name="America/Indianapolis",
            )
        )
    assert secret not in str(raised.value)

    _write_csv(tmp_path / "messages.csv", [_row()])
    inventory = inspect_imessages(tmp_path)
    with pytest.raises(IMessageAdapterError, match="unknown IANA timezone"):
        tuple(
            iter_imessage_exports(
                inventory.files[0],
                inventory,
                timezone_name="Not/A_Zone",
            )
        )


def test_unknown_reaction_or_message_type_cannot_report_production_ready(tmp_path: Path) -> None:
    _write_csv(
        tmp_path / "messages.csv",
        [_row(Reactions="unexpected private reaction syntax")],
    )
    with pytest.raises(IMessageAdapterError, match="unsupported structure"):
        _parsed(tmp_path)

    _write_csv(tmp_path / "messages.csv", [_row(Type="Future Type")])
    with pytest.raises(IMessageAdapterError, match="unsupported message type"):
        _parsed(tmp_path)


def test_orchestrator_is_complete_and_restart_short_circuit_safe(
    tmp_path: Path, monkeypatch
) -> None:
    _write_csv(tmp_path / "messages.csv", [_row(), _row(**{"Message Date": "2026-09-02 12:00:00"})])

    def imported(engine, batch):
        (parsed,) = batch
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
                short_circuited=True,
            ),
        )

    monkeypatch.setattr(orchestrator, "import_parsed_exports", imported)
    summary = orchestrator.import_imessages_all(
        object(), str(tmp_path), timezone_name="America/Indianapolis"
    )
    assert summary.production_ready
    assert summary.exit_code == 0
    assert summary.files_processed == summary.files_short_circuited == 1
    assert summary.messages == summary.records_discovered == 2


def test_cli_output_is_private_and_requires_timezone(tmp_path: Path, monkeypatch, capsys) -> None:
    secret = "PRIVATE CHAT NAME"
    _write_csv(tmp_path / f"{secret}.csv", [_row(**{"Chat Session": secret})])
    assert cli.main(["inspect-imessages", str(tmp_path)]) == 0
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert captured.err == ""

    monkeypatch.setattr(
        orchestrator,
        "import_parsed_exports",
        lambda engine, batch: (
            ImportResult(
                source_file_id=1,
                source_conversations=batch[0].conversation_count,
                database_conversations=batch[0].conversation_count,
                source_events=batch[0].event_count,
                database_events=batch[0].event_count,
                source_records=batch[0].record_count,
                database_records=batch[0].record_count,
                source_assets=batch[0].asset_count,
                database_assets=batch[0].asset_count,
                source_relationships=batch[0].relationship_count,
                database_relationships=batch[0].relationship_count,
            ),
        ),
    )
    monkeypatch.setattr(
        cli, "database_url", lambda override: "postgresql://user:pass@localhost/ignored"
    )
    monkeypatch.setattr(cli, "create_database_engine", lambda url: object())
    assert (
        cli.main(
            [
                "import-imessages-all",
                str(tmp_path),
                "--timezone",
                "America/Indianapolis",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
