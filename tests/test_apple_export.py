from __future__ import annotations

import io
import json
import warnings
from pathlib import Path
from zipfile import ZipFile

import pytest

from personal_data_lab import cli
from personal_data_lab.apple_export import orchestrator
from personal_data_lab.apple_export.adapters import ADAPTERS, AppleAdapterError
from personal_data_lab.apple_export.inventory import (
    AppleInventoryError,
    inspect_apple,
    open_apple_member,
    render_inventory_table,
)
from personal_data_lab.apple_export.products import PRODUCT_REGISTRY
from personal_data_lab.models import ImportResult


def _zip(path: Path, members: list[tuple[str, str | bytes]]) -> Path:
    with ZipFile(path, "w") as archive:
        for name, content in members:
            archive.writestr(name, content)
    return path


def _nested(members: list[tuple[str, str | bytes]]) -> bytes:
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Duplicate name:")
        with ZipFile(output, "w") as archive:
            for name, content in members:
                archive.writestr(name, content)
    return output.getvalue()


def _parsed(member):
    adapter = ADAPTERS[member.candidate_adapter]
    streaming = getattr(adapter, "iter_parsed_exports", None)
    if callable(streaming):
        return tuple(streaming(member))
    with open_apple_member(member) as stream:
        return (adapter.parse(stream, member),)


def test_registry_has_stable_observed_product_contract() -> None:
    assert len(PRODUCT_REGISTRY) == 32
    assert PRODUCT_REGISTRY["account_device"].display_name.startswith("Apple Account")
    assert PRODUCT_REGISTRY["notes"].display_name == "iCloud Notes"
    assert PRODUCT_REGISTRY["voice_memos"].display_name == "Voice Memos"


def test_recursive_inventory_accounts_for_nested_data_guides_and_bytes(tmp_path: Path) -> None:
    nested = _nested(
        [
            ("Apple Features Using iCloud/Voice Memos/assets/memo.m4a", b"audio"),
            ("Apple Features Using iCloud/Wi-Fi/KnownNetworks.xml", "<plist/>"),
        ]
    )
    source = _zip(
        tmp_path / "Other Data Part 5 of 6.zip",
        [
            ("Apple Features Using iCloud.zip", nested),
            ("Data and Privacy Guide.pdf", b"guide"),
        ],
    )
    inventory = inspect_apple(source)

    assert inventory.recursive_entries == 4
    assert inventory.classified_data_members == 2
    assert inventory.structural_members == 2
    assert inventory.nested_archive_containers == 1
    assert inventory.guide_members == 1
    assert inventory.unknown_members_count == 0
    assert inventory.accounted_members == inventory.recursive_entries
    assert inventory.physical_source_bytes == source.stat().st_size
    assert inventory.logical_leaf_bytes == len(b"audio") + len(b"<plist/>") + len(b"guide")
    assert "Physical source size" in render_inventory_table(inventory)


def test_duplicate_nested_member_names_are_distinct_and_open_exact_bytes(tmp_path: Path) -> None:
    nested = _nested([("assets/same.m4a", b"first"), ("assets/same.m4a", b"second-longer")])
    source = _zip(tmp_path / "Other Data Part 5 of 6.zip", [("Voice Memos.zip", nested)])
    assets = [item for item in inspect_apple(source).members if item.extension == ".m4a"]

    assert len(assets) == 2
    assert len({item.member_id for item in assets}) == 2
    with open_apple_member(assets[0]) as first, open_apple_member(assets[1]) as second:
        assert first.read() == b"first"
        assert second.read() == b"second-longer"
    parsed = [_parsed(item)[0] for item in assets]
    assert len({item.assets[0].sha256 for item in parsed}) == 2


def test_inventory_rejects_invalid_zip_and_marks_unsafe_path_unknown(tmp_path: Path) -> None:
    invalid = tmp_path / "broken.zip"
    invalid.write_bytes(b"not a zip")
    with pytest.raises(AppleInventoryError, match="invalid or unreadable ZIP"):
        inspect_apple(invalid)

    unsafe = _zip(
        tmp_path / "Apple Account and device information.zip",
        [("../../../escape.csv", "id,name\n1,Ada\n")],
    )
    inventory = inspect_apple(unsafe)
    assert inventory.unknown_members_count == 1
    assert inventory.unknown_members[0].reason == "unsafe_archive_path"


def test_public_inventory_redacts_paths_by_default(tmp_path: Path) -> None:
    secret = "PRIVATE NOTE TITLE"
    source = _zip(
        tmp_path / "iCloud Notes.zip",
        [(f"iCloud Notes/Notes/{secret}/note.txt", "private body")],
    )
    inventory = inspect_apple(source)

    assert secret not in json.dumps(inventory.public_dict())
    assert secret in json.dumps(inventory.public_dict(include_paths=True))


def test_csv_preserves_irregular_extra_columns_and_has_stable_occurrences(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "Apple Media Services Information Part 1 of 2.zip",
        [
            (
                "Apple_Media_Services/Apple Music Activity/history.csv",
                "Event ID,Event Date,Action\n1,2026-01-01,play,extra\n1,2026-01-01,play,extra\n",
            )
        ],
    )
    member = inspect_apple(source).members[0]
    (parsed,) = _parsed(member)

    assert parsed.record_count == 2
    assert parsed.event_count == 2
    assert parsed.records[0].source_record_id != parsed.records[1].source_record_id
    assert parsed.records[0].metadata["apple"]["_extra_columns"] == ["extra"]


def test_concatenated_json_and_mislabeled_utf16_xml_are_lossless(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "Other Data Part 5 of 6.zip",
        [
            ("Apple Features Using iCloud/iBooks/iBooks.json", '{"id":"1"}\n{"id":"2"}'),
            (
                "Apple Features Using iCloud/Wi-Fi/KnownNetworks.xml",
                '<?xml version="1.0" encoding="UTF-16"?><plist><network id="1"/></plist>',
            ),
        ],
    )
    members = inspect_apple(source).members

    books = _parsed(next(item for item in members if item.extension == ".json"))[0]
    wifi = _parsed(next(item for item in members if item.extension == ".xml"))[0]
    assert books.record_count == 2
    assert wifi.record_count == 1
    assert wifi.records[0].metadata["apple"]["tag"] == "network"


def test_contacts_calendar_notes_and_note_assets_are_semantic(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "iCloud Contacts.zip",
        [("iCloud Contacts/vCards/contact.vcf", "BEGIN:VCARD\nUID:c1\nFN:Ada\nEND:VCARD\n")],
    )
    contact = _parsed(inspect_apple(source).members[0])[0]
    assert contact.records[0].record_type == "contact"
    assert contact.records[0].title == "Ada"

    calendar_source = _zip(
        tmp_path / "iCloud Calendars and Reminders.zip",
        [
            (
                "iCloud Calendars and Reminders/Home.ics",
                "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:e1\nSUMMARY:Meet\nDTSTART:20260917T120000Z\nRRULE:FREQ=WEEKLY\nEND:VEVENT\nEND:VCALENDAR\n",
            )
        ],
    )
    calendar = _parsed(inspect_apple(calendar_source).members[0])[0]
    assert calendar.records[0].record_type == "calendar_event"
    assert calendar.records[0].metadata["apple"]["properties"]["RRULE"] == ["FREQ=WEEKLY"]

    notes_source = _zip(
        tmp_path / "iCloud Notes.zip",
        [
            ("iCloud Notes/Notes/A Note/note.txt", "body"),
            ("iCloud Notes/Notes/A Note/image.jpg", b"image"),
        ],
    )
    note_members = inspect_apple(notes_source).members
    note = _parsed(next(item for item in note_members if item.extension == ".txt"))[0]
    image = _parsed(next(item for item in note_members if item.extension == ".jpg"))[0]
    container = next(item for item in note.records if item.record_type == "note_container")
    note_record = next(item for item in note.records if item.record_type == "note")
    assert note_record.text == "body"
    assert image.record_count == image.asset_count == image.relationship_count == 1
    assert note.relationships[0].subject_source_id == container.source_record_id
    assert image.relationships[0].subject_source_id == container.source_record_id


def test_multiple_note_text_files_in_one_directory_have_distinct_identities(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "iCloud Notes.zip",
        [
            ("iCloud Notes/Notes/One Folder/first.txt", "first"),
            ("iCloud Notes/Notes/One Folder/second.txt", "second"),
        ],
    )
    parsed = [_parsed(item)[0] for item in inspect_apple(source).members]
    note_ids = {
        record.source_record_id
        for export in parsed
        for record in export.records
        if record.record_type == "note"
    }
    container_ids = {
        record.source_record_id
        for export in parsed
        for record in export.records
        if record.record_type == "note_container"
    }
    assert len(note_ids) == 2
    assert len(container_ids) == 1


def test_malformed_structured_error_never_contains_payload(tmp_path: Path) -> None:
    secret = "DO NOT LOG THIS PAYLOAD"
    source = _zip(
        tmp_path / "Apple Account and device information.zip",
        [("Apple Account and device information/data.json", '{"secret":"' + secret)],
    )
    member = inspect_apple(source).members[0]
    with open_apple_member(member) as stream, pytest.raises(AppleAdapterError) as raised:
        ADAPTERS["apple_structured"].parse(stream, member)
    assert secret not in str(raised.value)


def test_orchestrator_complete_coverage_and_short_circuit(tmp_path: Path, monkeypatch) -> None:
    source = _zip(
        tmp_path / "Apple Account and device information.zip",
        [
            (
                "Apple Account and device information/devices.csv",
                "Device Identifier,Date\nd1,2026-01-01\n",
            )
        ],
    )

    def imported(engine, batch):
        (parsed,) = batch
        return (
            ImportResult(
                source_file_id=1,
                source_conversations=0,
                database_conversations=0,
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
    summary = orchestrator.import_apple_all(object(), str(source))
    assert summary.members_imported == summary.members_short_circuited == 1
    assert summary.complete_accounting
    assert summary.production_ready
    assert summary.exit_code == 0
    assert summary.coverage["account_device"].status == "fully_structured"
    assert summary.coverage["notes"].status == "not_present_in_export"


def test_apple_cli_defaults_are_private_and_machine_readable(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    secret = "PRIVATE TITLE"
    source = _zip(
        tmp_path / "iCloud Notes.zip",
        [(f"iCloud Notes/Notes/{secret}/note.txt", "PRIVATE BODY")],
    )
    assert cli.main(["inspect-apple", str(source)]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["classified_data_members"] == 1
    assert secret not in captured.out
    assert captured.err == ""

    monkeypatch.setattr(
        orchestrator,
        "import_parsed_exports",
        lambda engine, batch: (
            ImportResult(
                source_file_id=1,
                source_conversations=0,
                database_conversations=0,
                source_events=0,
                database_events=0,
                source_records=batch[0].record_count,
                database_records=batch[0].record_count,
            ),
        ),
    )
    monkeypatch.setattr(cli, "database_url", lambda override: "ignored")
    monkeypatch.setattr(cli, "create_database_engine", lambda url: object())
    assert cli.main(["import-apple-all", str(source)]) == 0
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    assert "PRIVATE BODY" not in captured.out
    assert "PRIVATE BODY" not in captured.err
