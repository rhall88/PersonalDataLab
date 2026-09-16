from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

import pytest

from personal_data_lab import cli
from personal_data_lab.google_takeout import orchestrator
from personal_data_lab.google_takeout.adapters import (
    DEFAULT_ADAPTER_REGISTRY,
    AdapterAmbiguityError,
    AdapterRegistry,
    GenericStructuredAdapter,
    GoogleAdapterError,
    MatchResult,
    open_takeout_member,
)
from personal_data_lab.google_takeout.inventory import (
    GoogleInventoryError,
    inspect_google,
    render_inventory_table,
)
from personal_data_lab.google_takeout.products import (
    EXPECTED_68_DISPLAY_NAMES,
    EXPECTED_68_PRODUCTS,
    PRODUCT_REGISTRY,
)
from personal_data_lab.models import ImportResult


def _zip(path: Path, members: dict[str, str | bytes]) -> Path:
    with ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


def test_registry_is_the_exact_68_product_contract() -> None:
    assert len(EXPECTED_68_PRODUCTS) == 68
    assert len(EXPECTED_68_DISPLAY_NAMES) == 68
    assert set(EXPECTED_68_PRODUCTS) == set(PRODUCT_REGISTRY)
    assert EXPECTED_68_DISPLAY_NAMES[0] == "Access Log Activity"
    assert EXPECTED_68_DISPLAY_NAMES[-1] == "YouTube and YouTube Music"
    for key, product in PRODUCT_REGISTRY.items():
        assert product.canonical_product_key == key
        assert product.takeout_display_names
        assert product.adapter
        assert product.support_level
        assert product.expected_format_families
        assert product.record_kinds
        assert product.notes


def test_inventory_accounts_for_every_zip_member_without_opening_payloads(
    tmp_path: Path, monkeypatch
) -> None:
    source = _zip(
        tmp_path / "takeout-20260909T012526Z-001.zip",
        {
            "Takeout/": b"",
            "Takeout/archive_browser.html": "structural",
            "Takeout/My Activity/Search/MyActivity.json": '[{"title":"private"}]',
            "Takeout/Calendar/data.json": '[{"id":"event-1"}]',
            "Takeout/Drive/video.bin": b"\x00\x01\x02",
            "Takeout/Future Product/new.xyz": b"future",
        },
    )

    def reject_open(*args, **kwargs):  # pragma: no cover - failure proves content was read
        raise AssertionError("inventory must not open member bytes")

    monkeypatch.setattr(ZipFile, "open", reject_open)
    inventory = inspect_google(source)

    assert inventory.all_archive_members == 6
    assert inventory.accounted_members == 6
    assert inventory.classified_members == 3
    assert inventory.intentionally_ignored_structural_members == 2
    assert inventory.unknown_members_count == 1
    assert inventory.export_ids == ("20260909T012526Z",)
    activity = next(member for member in inventory.members if member.filename == "MyActivity.json")
    assert activity.archive_ordinal == 1
    assert activity.candidate_adapter == "my_activity"
    assert activity.support_status == "fully_structured"
    assert activity.compressed_size is not None
    assert activity.zip_crc is not None
    calendar = next(member for member in inventory.members if member.filename == "data.json")
    assert calendar.product_key == "calendar"
    assert calendar.candidate_adapter == "generic_structured"
    assert calendar.support_status == "partially_structured"
    drive = next(member for member in inventory.members if member.filename == "video.bin")
    assert drive.product_key == "drive"
    assert drive.appears_binary
    assert drive.support_status == "raw_preserved"
    assert "Accounted members: 6/6" in render_inventory_table(inventory)


def test_my_activity_binary_members_are_raw_preserved_not_parsed(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260909T012526Z-001.zip",
        {
            "Takeout/My Activity/Search/MyActivity.html": "<html></html>",
            "Takeout/My Activity/Search/voice.wav": b"RIFF-private-binary",
            "Takeout/My Activity/Google Lens/image.jpg": b"private-image",
        },
    )

    inventory = inspect_google(source)
    activity = next(member for member in inventory.members if member.extension == ".html")
    assert activity.candidate_adapter == "my_activity"
    assert activity.support_status == "fully_structured"
    assert DEFAULT_ADAPTER_REGISTRY.resolve(activity) is not None

    for extension in (".wav", ".jpg"):
        attachment = next(
            member for member in inventory.members if member.extension == extension
        )
        assert attachment.candidate_adapter is None
        assert attachment.support_status == "raw_preserved"
        assert attachment.reason == "no_pr5_activity_pattern"
        assert DEFAULT_ADAPTER_REGISTRY.resolve(attachment) is None


def test_inventory_handles_split_archives_and_browser_download_duplicates(tmp_path: Path) -> None:
    first = _zip(
        tmp_path / "takeout-20260909T012526Z-2-001.zip",
        {"Takeout/Calendar/a.json": "[]"},
    )
    second = _zip(
        tmp_path / "takeout-20260909T012526Z-002 (1).zip",
        {"Takeout/Contacts/b.csv": "id,name\n1,Ada\n"},
    )
    inventory = inspect_google(tmp_path)

    assert inventory.archives == 2
    assert inventory.export_ids == ("20260909T012526Z",)
    assert {member.archive_ordinal for member in inventory.members} == {1, 2}
    assert {member.archive_path for member in inventory.members} == {str(first), str(second)}
    assert inventory.accounted_members == inventory.all_archive_members == 2


def test_inventory_handles_extracted_takeout_and_empty_product(tmp_path: Path) -> None:
    takeout = tmp_path / "takeout-20260909T012526Z" / "Takeout"
    (takeout / "Calendar").mkdir(parents=True)
    contacts = takeout / "Contacts" / "contacts.csv"
    contacts.parent.mkdir()
    contacts.write_text("id,name\n1,Ada\n", encoding="utf-8")

    inventory = inspect_google(takeout.parent)

    assert inventory.archives == 1
    assert inventory.accounted_members == inventory.all_archive_members
    assert any(item.product_key == "calendar" and item.files == 0 for item in inventory.products)
    member = next(item for item in inventory.members if item.filename == "contacts.csv")
    assert member.container_kind == "extracted"
    assert Path(member.source_locator) == contacts
    with open_takeout_member(member) as stream:
        assert stream.read().decode("utf-8").splitlines() == ["id,name", "1,Ada"]


def test_inventory_rejects_invalid_zip_and_marks_unsafe_member_unknown(tmp_path: Path) -> None:
    invalid = tmp_path / "takeout-20260909T012526Z-001.zip"
    invalid.write_bytes(b"not a zip")
    with pytest.raises(GoogleInventoryError, match="invalid or unreadable ZIP"):
        inspect_google(invalid)

    unsafe = _zip(
        tmp_path / "takeout-20260909T012526Z-002.zip",
        {"Takeout/Calendar/../../../escape.json": "[]"},
    )
    inventory = inspect_google(unsafe)
    assert inventory.unknown_members_count == 1
    assert inventory.unknown_members[0].reason == "unsafe_archive_path"


def test_generic_adapter_preserves_fields_and_has_deterministic_identity(tmp_path: Path) -> None:
    payload = [
        {
            "id": "provider-1",
            "title": "PRIVATE TITLE",
            "unknown": {"future": 42},
            "updatedAt": "2026-09-01T12:34:56-04:00",
        },
        {"name": "No provider id", "custom": True, "date": "09/10/2026"},
    ]
    source = _zip(
        tmp_path / "takeout-20260902T000000Z-001.zip",
        {"Takeout/Calendar/data.json": json.dumps(payload)},
    )
    member = inspect_google(source).members[0]
    adapter = DEFAULT_ADAPTER_REGISTRY.resolve(member)
    assert isinstance(adapter, GenericStructuredAdapter)
    with open_takeout_member(member) as stream:
        parsed = adapter.parse(stream, member)

    assert parsed.record_count == 2
    assert parsed.records[0].source_record_id == "provider:provider-1"
    assert parsed.records[0].updated_at == datetime(2026, 9, 1, 16, 34, 56, tzinfo=UTC)
    assert parsed.records[0].metadata["google"]["unknown"] == {"future": 42}
    assert parsed.records[0].metadata["_pdl"]["generic_adapter"] is True
    assert parsed.records[1].source_record_id.startswith("content:")
    assert "PRIVATE TITLE" in (parsed.records[0].text or "")
    assert parsed.parse_warnings == ("record 1 occurred_at could not be normalized",)

    renamed = _zip(
        tmp_path / "takeout-20260902T000000Z-009.zip",
        {"Takeout/Calendar/renamed.json": json.dumps(payload)},
    )
    renamed_member = inspect_google(renamed).members[0]
    with open_takeout_member(renamed_member) as stream:
        reparsed = adapter.parse(stream, renamed_member)
    assert [record.source_record_id for record in reparsed.records] == [
        record.source_record_id for record in parsed.records
    ]


def test_generic_adapter_supports_csv_xml_empty_and_duplicate_ids(tmp_path: Path) -> None:
    csv_source = _zip(
        tmp_path / "takeout-20260902T000000Z-001.zip",
        {"Takeout/Contacts/data.csv": "id,name\n1,Ada\n1,Grace\n"},
    )
    csv_member = inspect_google(csv_source).members[0]
    adapter = GenericStructuredAdapter()
    with open_takeout_member(csv_member) as stream:
        parsed_csv = adapter.parse(stream, csv_member)
    assert [record.source_record_id for record in parsed_csv.records] == [
        "provider:1",
        "provider:1:2",
    ]
    assert parsed_csv.records[0].metadata["google"]["fields"] == {"id": "1", "name": "Ada"}

    xml_source = _zip(
        tmp_path / "takeout-20260902T000000Z-002.zip",
        {"Takeout/Blogger/data.xml": '<feed future="yes"><entry>hello</entry></feed>'},
    )
    xml_member = inspect_google(xml_source).members[0]
    with open_takeout_member(xml_member) as stream:
        parsed_xml = adapter.parse(stream, xml_member)
    assert parsed_xml.record_count == 1
    assert parsed_xml.records[0].metadata["google"]["raw_xml"].startswith("<feed")

    empty_source = _zip(
        tmp_path / "takeout-20260902T000000Z-003.zip",
        {"Takeout/Calendar/empty.json": "[]"},
    )
    empty_member = inspect_google(empty_source).members[0]
    with open_takeout_member(empty_member) as stream:
        assert adapter.parse(stream, empty_member).record_count == 0

    blank_source = _zip(
        tmp_path / "takeout-20260902T000000Z-004.zip",
        {
            "Takeout/Workspace Studio/empty.json": b"",
            "Takeout/Maps/whitespace.json": "\n",
        },
    )
    for blank_member in inspect_google(blank_source).members:
        with open_takeout_member(blank_member) as stream:
            parsed_blank = adapter.parse(stream, blank_member)
        assert parsed_blank.record_count == 0


def test_generic_adapter_rejects_malformed_input_without_payload_in_error(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260902T000000Z-001.zip",
        {"Takeout/Calendar/broken.json": '{"private":"DO NOT LOG"'},
    )
    member = inspect_google(source).members[0]
    with open_takeout_member(member) as stream, pytest.raises(
        GoogleAdapterError, match="could not parse generic structured member"
    ) as raised:
        GenericStructuredAdapter().parse(stream, member)
    assert "DO NOT LOG" not in str(raised.value)


def test_adapter_ambiguity_is_an_error(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260902T000000Z-001.zip",
        {"Takeout/Calendar/data.json": "[]"},
    )
    member = inspect_google(source).members[0]

    class Always:
        parser_version = "test-v1"
        product_keys = ("calendar",)
        supported_patterns = ("*",)
        support_level = "fully_structured"
        representation_kind = "records"
        is_fallback = False

        def __init__(self, name: str) -> None:
            self.adapter_name = name

        def probe(self, member):
            return MatchResult(True, "synthetic overlap")

        def parse(self, stream, member):  # pragma: no cover - ambiguity stops first
            raise AssertionError

    registry = AdapterRegistry((Always("a"), Always("b")))
    with pytest.raises(AdapterAmbiguityError, match="multiple adapters"):
        registry.resolve(member)


def test_orchestrator_reports_full_partial_raw_and_unknown_coverage(
    tmp_path: Path, monkeypatch
) -> None:
    source = _zip(
        tmp_path / "takeout-20260902T000000Z-001.zip",
        {
            "Takeout/My Activity/Search/MyActivity.json": '[{"title":"Search"}]',
            "Takeout/Calendar/data.json": '[{"id":"calendar-1"}]',
            "Takeout/Drive/video.bin": b"\x00",
            "Takeout/Future Product/data.json": "[]",
        },
    )

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
                inserted_events=parsed.event_count,
                inserted_records=parsed.record_count,
            ),
        )

    monkeypatch.setattr(orchestrator, "import_parsed_exports", imported)
    summary = orchestrator.import_google_all(object(), str(source))

    assert summary.members_total == 4
    assert summary.members_imported == 2
    assert summary.members_short_circuited == 0
    assert summary.members_raw_preserved == 1
    assert summary.members_unknown == 1
    assert summary.exit_code == 5
    assert summary.coverage["my_activity"].support_status == "fully_structured"
    assert summary.coverage["calendar"].support_status == "partially_structured"
    assert summary.coverage["drive"].support_status == "raw_preserved"
    assert summary.coverage["contacts"].support_status == "not_present_in_export"


def test_orchestrator_reports_short_circuit_as_reuse_not_new_insert(
    tmp_path: Path, monkeypatch
) -> None:
    source = _zip(
        tmp_path / "takeout-20260902T000000Z-001.zip",
        {"Takeout/Calendar/data.json": '[{"id":"calendar-1"}]'},
    )

    def short_circuited(engine, batch):
        (parsed,) = batch
        return (
            ImportResult(
                source_file_id=1,
                source_conversations=0,
                database_conversations=0,
                source_events=0,
                database_events=0,
                source_records=parsed.record_count,
                database_records=parsed.record_count,
                inserted_records=parsed.record_count,
                new_record_versions=parsed.record_count,
                short_circuited=True,
            ),
        )

    monkeypatch.setattr(orchestrator, "import_parsed_exports", short_circuited)
    summary = orchestrator.import_google_all(object(), str(source))
    coverage = summary.coverage["calendar"]

    assert summary.members_short_circuited == 1
    assert coverage.members_short_circuited == 1
    assert coverage.inserted == 0
    assert coverage.unchanged == 1
    assert coverage.version_count == 0
    assert coverage.reused_versions == 1


def test_inspect_google_cli_emits_clean_json_and_optional_table(tmp_path: Path, capsys) -> None:
    source = _zip(
        tmp_path / "takeout-20260902T000000Z-001.zip",
        {"Takeout/Calendar/data.json": "[]"},
    )
    assert cli.main(["inspect-google", str(source)]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["all_archive_members"] == 1
    assert payload["classified_members"] == 1
    assert captured.err == ""

    assert cli.main(["inspect-google", str(source), "--format", "table"]) == 0
    captured = capsys.readouterr()
    assert "Google Takeout Inventory" in captured.out
    assert "Calendar" in captured.out
    assert captured.err == ""


def test_import_google_all_cli_does_not_log_source_content(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    source = _zip(
        tmp_path / "takeout-20260902T000000Z-001.zip",
        {"Takeout/Calendar/data.json": '[{"id":"1","text":"PRIVATE PAYLOAD"}]'},
    )

    def imported(engine, batch):
        (parsed,) = batch
        return (
            ImportResult(
                source_file_id=1,
                source_conversations=0,
                database_conversations=0,
                source_events=0,
                database_events=0,
                source_records=parsed.record_count,
                database_records=parsed.record_count,
                source_assets=0,
                database_assets=0,
                inserted_records=parsed.record_count,
            ),
        )

    monkeypatch.setattr(orchestrator, "import_parsed_exports", imported)
    monkeypatch.setattr(cli, "database_url", lambda override: "ignored")
    monkeypatch.setattr(cli, "create_database_engine", lambda url: object())
    assert cli.main(["import-google-all", str(source)]) == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert summary["members_imported"] == 1
    assert "PRIVATE PAYLOAD" not in captured.out
    assert "PRIVATE PAYLOAD" not in captured.err
