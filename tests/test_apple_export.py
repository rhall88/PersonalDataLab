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
    assert wifi.records[0].metadata["apple"]["tag"] == "plist"
    assert wifi.records[0].metadata["apple"]["children"][0]["tag"] == "network"


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
    assert not summary.production_ready
    assert summary.exit_code == 3
    assert summary.coverage["account_device"].status == "partially_structured"
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


def test_identical_note_bytes_have_distinct_replay_keys(tmp_path: Path) -> None:
    from personal_data_lab.database import _run_key

    source = _zip(
        tmp_path / "iCloud Notes.zip",
        [
            ("Notes/A/note.txt", "same body"),
            ("Notes/B/note.txt", "same body"),
            ("Notes/A/image.jpg", b"same image"),
            ("Notes/B/image.jpg", b"same image"),
        ],
    )
    parsed = [_parsed(member)[0] for member in inspect_apple(source).members]
    assert parsed[0].sha256 == parsed[1].sha256
    assert len({_run_key((item,)) for item in parsed}) == 4
    assert parsed[2].assets[0].source_asset_id == parsed[3].assets[0].source_asset_id
    assert (
        parsed[2].relationships[0].source_relationship_id
        != parsed[3].relationships[0].source_relationship_id
    )


def test_repackaging_and_row_reordering_preserve_identity_and_fingerprint(tmp_path: Path) -> None:
    from personal_data_lab.fingerprints import record_fingerprint

    header = "Device Identifier,Event Date,Action\n"
    rows = ["d1,2026-01-01,play\n", "d1,2026-01-02,stop\n"]
    source = _zip(tmp_path / "Other Data Part 1.zip", [("history.csv", header + "".join(rows))])
    before = _parsed(inspect_apple(source).members[0])[0]
    source = _zip(
        tmp_path / "Other Data Part 2.zip",
        [
            ("unrelated.txt", "added"),
            ("history.csv", header + "".join(reversed(rows))),
        ],
    )
    after = _parsed(inspect_apple(source).members[1])[0]
    expected = {record.source_record_id: record_fingerprint(record) for record in before.records}
    assert expected == {
        record.source_record_id: record_fingerprint(record) for record in after.records
    }
    source = _zip(source, [("history.csv", header + rows[1])])
    smaller = _parsed(inspect_apple(source).members[0])[0]
    assert expected[smaller.records[0].source_record_id] == record_fingerprint(smaller.records[0])


def test_context_key_survives_unrelated_nested_zip_reordering(tmp_path: Path) -> None:
    from personal_data_lab.database import _run_key

    source = _zip(
        tmp_path / "iCloud Notes.zip",
        [
            (
                "notes.zip",
                _nested(
                    [
                        ("Notes/A/note.txt", "body"),
                    ]
                ),
            )
        ],
    )
    a = _parsed(next(item for item in inspect_apple(source).members if item.is_leaf))[0]
    source = _zip(
        source,
        [
            ("unrelated.txt", "x"),
            (
                "notes.zip",
                _nested(
                    [
                        ("new.txt", "y"),
                        ("Notes/A/note.txt", "body"),
                    ]
                ),
            ),
        ],
    )
    b = _parsed(
        next(item for item in inspect_apple(source).members if item.filename == "note.txt")
    )[0]
    assert _run_key((a,)) == _run_key((b,))
    assert a.records == b.records


def test_nested_locator_roundtrips_duplicate_container_names(tmp_path: Path) -> None:
    from personal_data_lab.apple_export.inventory import open_apple_locator

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        source = _zip(
            tmp_path / "Other Data.zip",
            [
                ("Voice Memos.zip", _nested([("a!/#entry=1.m4a", b"first")])),
                ("Voice Memos.zip", _nested([("a!/#entry=1.m4a", b"second")])),
            ],
        )
    members = [member for member in inspect_apple(source).members if member.is_leaf]
    assert len({member.source_locator for member in members}) == 2
    for member, expected in zip(members, (b"first", b"second"), strict=True):
        with open_apple_locator(member.source_locator) as stream:
            assert stream.read() == expected


def test_duplicate_note_entry_names_are_distinct(tmp_path: Path) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        source = _zip(
            tmp_path / "iCloud Notes.zip",
            [
                ("Notes/A/note.txt", "first"),
                ("Notes/A/note.txt", "second"),
            ],
        )
    records = [
        record
        for member in inspect_apple(source).members
        for record in _parsed(member)[0].records
        if record.record_type == "note"
    ]
    assert len({record.source_record_id for record in records}) == 2


def test_calendar_preserves_parameters_nested_alarms_and_recurrence_identity(
    tmp_path: Path,
) -> None:
    source = _zip(
        tmp_path / "iCloud Calendars and Reminders.zip",
        [
            (
                "Home.ics",
                (
                    "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:e1\n"
                    "DTSTART;TZID=America/New_York:20260917T090000\nSUMMARY:Meeting\n"
                    "BEGIN:VALARM\nDESCRIPTION:Alarm\nEND:VALARM\nEND:VEVENT\n"
                    "BEGIN:VEVENT\nUID:e1\nRECURRENCE-ID;TZID=America/New_York:20260924T090000\n"
                    "DTSTART;TZID=America/New_York:20260924T100000\nEND:VEVENT\nEND:VCALENDAR\n"
                ),
            )
        ],
    )
    parsed = _parsed(inspect_apple(source).members[0])[0]
    assert parsed.events[0].occurred_at.isoformat() == "2026-09-17T13:00:00+00:00"
    apple = parsed.events[0].metadata["apple"]
    assert apple["property_details"]["DTSTART"][0]["params"] == {"TZID": ["America/New_York"]}
    assert "DESCRIPTION" not in apple["properties"]
    assert "DESCRIPTION:Alarm" in apple["lines"]
    assert len({event.source_record_id for event in parsed.events}) == 2


@pytest.mark.parametrize("date", ["DTSTART:20260917T090000", "DTSTART;VALUE=DATE:20260917"])
def test_calendar_does_not_invent_instants_for_floating_or_all_day_dates(
    tmp_path: Path, date: str
) -> None:
    source = _zip(
        tmp_path / "iCloud Calendars and Reminders.zip",
        [
            (
                "Home.ics",
                f"BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:e1\n{date}\nEND:VEVENT\nEND:VCALENDAR\n",
            )
        ],
    )
    parsed = _parsed(inspect_apple(source).members[0])[0]
    assert parsed.events[0].occurred_at is None
    assert not parsed.parse_warnings


def test_contact_parameters_are_retained(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "iCloud Contacts.zip",
        [
            (
                "contact.vcf",
                (
                    "BEGIN:VCARD\nUID:c1\nFN:Ada\nTEL;TYPE=HOME,VOICE:123\n"
                    'EMAIL;TYPE="work;preferred":ada@example.invalid\nEND:VCARD\n'
                ),
            )
        ],
    )
    props = (
        _parsed(inspect_apple(source).members[0])[0]
        .records[0]
        .metadata["apple"]["property_details"]
    )
    assert props["TEL"][0]["params"] == {"TYPE": ["HOME,VOICE"]}
    assert props["EMAIL"][0]["params"] == {"TYPE": ["work;preferred"]}


@pytest.mark.parametrize(
    "filename,body",
    [
        ("broken.vcf", "BEGIN:VCARD\nUID:c1\nFN:Lost\n"),
        ("broken.ics", "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:e1\nEND:VCALENDAR\n"),
        ("broken.ics", "not a calendar"),
    ],
)
def test_malformed_components_fail_instead_of_succeeding_empty(
    tmp_path: Path, filename: str, body: str
) -> None:
    source = _zip(tmp_path / "Other Data.zip", [(filename, body)])
    with pytest.raises(AppleAdapterError):
        _parsed(inspect_apple(source).members[0])


def test_valid_empty_calendar_is_accepted(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "Other Data.zip",
        [("empty.ics", "BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n")],
    )
    assert _parsed(inspect_apple(source).members[0])[0].record_count == 0


def test_whitespace_only_csv_is_retained_as_empty_dataset(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "TestFlight.zip",
        [("Testflight Data History-sessions.csv", "\n")],
    )

    (parsed,) = _parsed(inspect_apple(source).members[0])

    assert parsed.category.endswith("/chunk/00000001")
    assert parsed.record_count == 0
    assert parsed.event_count == 0


def test_xml_preserves_root_mixed_order_whitespace_and_tail(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "Other Data.zip",
        [
            (
                "data.xml",
                '<root account="A"> prefix <a>one</a>tail<b/><a>two</a><!--comment--></root>',
            )
        ],
    )
    root = _parsed(inspect_apple(source).members[0])[0].records[0].metadata["apple"]
    assert root["attributes"] == {"account": "A"}
    assert root["text"] == " prefix "
    assert [child["tag"] for child in root["children"]] == ["a", "b", "a", "#comment"]
    assert root["children"][0]["tail"] == "tail"


@pytest.mark.parametrize(
    "csv_text,headers,cells",
    [
        ("id,value,value\n1,first,second\n", ["id", "value", "value"], ["1", "first", "second"]),
        (
            "id,_extra_columns\n1,value,surplus\n",
            ["id", "_extra_columns"],
            ["1", "value", "surplus"],
        ),
    ],
)
def test_csv_preserves_duplicate_and_reserved_headers(
    tmp_path: Path, csv_text, headers, cells
) -> None:
    source = _zip(tmp_path / "Other Data.zip", [("data.csv", csv_text)])
    apple = _parsed(inspect_apple(source).members[0])[0].records[0].metadata["apple"]
    assert apple == {"headers": headers, "cells": cells}


@pytest.mark.parametrize("outer", ["iCloud Notes.zip", "icloud-notes.zip", "amp-custom.zip"])
def test_user_pdf_guides_are_assets(tmp_path: Path, outer: str) -> None:
    source = _zip(
        tmp_path / outer,
        [
            ("Notes/Travel guide/Trip.pdf", b"personal"),
            ("Travel guide.pdf", b"personal"),
            ("Notes/A/Data and Privacy Guide.pdf", b"personal"),
        ],
    )
    assert all(
        member.candidate_adapter == "apple_asset" for member in inspect_apple(source).members
    )


def test_inventory_source_path_requires_opt_in(tmp_path: Path) -> None:
    private = tmp_path / "PRIVATE ACCOUNT DIRECTORY"
    private.mkdir()
    source = _zip(private / "iCloud Notes.zip", [("Notes/A/note.txt", "body")])
    inventory = inspect_apple(source)
    assert "PRIVATE ACCOUNT DIRECTORY" not in json.dumps(inventory.public_dict())
    assert inventory.public_dict(include_paths=True)["source_path"] == str(source)


def test_warnings_prevent_production_ready_and_success_exit(tmp_path: Path, monkeypatch) -> None:
    source = _zip(
        tmp_path / "iCloud Calendars and Reminders.zip",
        [
            (
                "Home.ics",
                "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:e1\nDTSTART;TZID=Unknown/Zone:20260917T090000\nEND:VEVENT\nEND:VCALENDAR\n",
            )
        ],
    )
    monkeypatch.setattr(
        orchestrator,
        "import_parsed_exports",
        lambda engine, batch: (
            ImportResult(
                source_file_id=1,
                source_conversations=0,
                database_conversations=0,
                source_events=1,
                database_events=1,
            ),
        ),
    )
    summary = orchestrator.import_apple_all(object(), str(source))
    assert summary.coverage["calendars_reminders"].parse_warnings == 1
    assert summary.exit_code == 3
    assert not summary.production_ready
