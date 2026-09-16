from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

from personal_data_lab.google_takeout.adapters import (
    DEFAULT_ADAPTER_REGISTRY,
    open_takeout_member,
)
from personal_data_lab.google_takeout.inventory import inspect_google


def _zip(path: Path, members: dict[str, str | bytes]) -> Path:
    with ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


def _parsed_member(path: Path, suffix: str):
    inventory = inspect_google(path)
    member = next(item for item in inventory.members if item.member_path.endswith(suffix))
    adapter = DEFAULT_ADAPTER_REGISTRY.resolve(member)
    assert adapter is not None
    with open_takeout_member(member) as stream:
        return adapter, adapter.parse(stream, member)


def test_calendar_ics_preserves_definition_and_projects_only_one_off_event(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260909T012526Z-001.zip",
        {
            "Takeout/Calendar/Example.ics": (
                "BEGIN:VCALENDAR\r\n"
                "VERSION:2.0\r\n"
                "X-WR-CALNAME:Synthetic Calendar\r\n"
                "BEGIN:VEVENT\r\n"
                "UID:event-1@example.invalid\r\n"
                "DTSTART;TZID=America/New_York:20260918T090000\r\n"
                "DTEND;TZID=America/New_York:20260918T100000\r\n"
                "SUMMARY:Synthetic appointment\r\n"
                "ATTACH;FMTTYPE=application/pdf:https://example.invalid/agenda.pdf\r\n"
                "X-FUTURE-FIELD:preserved\r\n"
                "END:VEVENT\r\n"
                "BEGIN:VEVENT\r\n"
                "UID:event-2@example.invalid\r\n"
                "DTSTART:20260919T120000Z\r\n"
                "RRULE:FREQ=WEEKLY;COUNT=3\r\n"
                "SUMMARY:Synthetic recurring event\r\n"
                "END:VEVENT\r\n"
                "END:VCALENDAR"
            )
        },
    )

    adapter, parsed = _parsed_member(source, "Example.ics")

    assert adapter.adapter_name == "calendar_semantic"
    assert [record.record_type for record in parsed.records] == [
        "calendar",
        "calendar_event_definition",
        "calendar_event_definition",
    ]
    assert len(parsed.events) == 1
    assert parsed.events[0].occurred_at.isoformat() == "2026-09-18T13:00:00+00:00"
    assert len(parsed.relationships) == 3
    assert len(parsed.assets) == 1
    assert parsed.assets[0].mime_type == "application/pdf"
    assert {relationship.relationship_type for relationship in parsed.relationships} == {
        "calendar_contains_event_definition",
        "calendar_event_definition_has_asset",
    }
    assert "X-FUTURE-FIELD" in parsed.records[1].metadata["google"]


def test_contacts_vcard_emits_groups_relationships_and_photo_reference(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260909T012526Z-001.zip",
        {
            "Takeout/Contacts/All Contacts/All Contacts.vcf": (
                "BEGIN:VCARD\r\n"
                "VERSION:3.0\r\n"
                "UID:contact-1\r\n"
                "FN:Synthetic Person\r\n"
                "EMAIL:person@example.invalid\r\n"
                "CATEGORIES:Friends,Synthetic Group\r\n"
                "PHOTO;VALUE=URI:https://example.invalid/photo.jpg\r\n"
                "X-FUTURE-FIELD:preserved\r\n"
                "END:VCARD"
            )
        },
    )

    adapter, parsed = _parsed_member(source, "All Contacts.vcf")

    assert adapter.adapter_name == "contacts_semantic"
    assert [record.record_type for record in parsed.records].count("contact") == 1
    assert [record.record_type for record in parsed.records].count("contact_group") == 2
    assert len(parsed.relationships) == 2
    assert {item.relationship_type for item in parsed.relationships} == {
        "contact_group_contains_contact"
    }
    assert len(parsed.assets) == 1
    assert parsed.assets[0].original_path == "https://example.invalid/photo.jpg"
    assert "X-FUTURE-FIELD" in parsed.records[0].metadata["google"]


def test_tasks_json_preserves_lists_tasks_parent_links_and_unknown_fields(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260909T012526Z-001.zip",
        {
            "Takeout/Tasks/Tasks.json": json.dumps(
                {
                    "taskLists": [
                        {
                            "id": "list-1",
                            "title": "Synthetic list",
                            "futureListField": {"preserved": True},
                            "tasks": [
                                {
                                    "id": "task-1",
                                    "title": "Parent task",
                                    "updated": "2026-09-16T12:00:00Z",
                                    "futureTaskField": [1, 2, 3],
                                },
                                {
                                    "id": "task-2",
                                    "title": "Child task",
                                    "parent": "task-1",
                                    "completed": "2026-09-17T12:00:00Z",
                                },
                            ],
                        }
                    ]
                }
            )
        },
    )

    adapter, parsed = _parsed_member(source, "Tasks.json")

    assert adapter.adapter_name == "tasks_semantic"
    assert [record.record_type for record in parsed.records] == ["task_list", "task", "task"]
    assert [item.relationship_type for item in parsed.relationships].count(
        "task_list_contains_task"
    ) == 2
    assert [item.relationship_type for item in parsed.relationships].count(
        "task_parent_contains_child"
    ) == 1
    assert parsed.records[1].metadata["google"]["futureTaskField"] == [1, 2, 3]


def test_saved_csv_emits_semantic_items_and_ordered_list_membership(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260909T012526Z-001.zip",
        {
            "Takeout/Saved/Synthetic list.csv": (
                "id,name,latitude,longitude,future\r\n"
                "place-1,Synthetic Place,39.0,-86.0,preserved\r\n"
            )
        },
    )

    adapter, parsed = _parsed_member(source, "Synthetic list.csv")

    assert adapter.adapter_name == "geo_semantic"
    assert [record.record_type for record in parsed.records] == ["saved_list", "saved_item"]
    assert parsed.records[1].metadata["_pdl"]["geo"] == {
        "latitude": 39.0,
        "longitude": -86.0,
        "source_precision": "provider_value",
    }
    assert parsed.records[1].metadata["google"]["future"] == "preserved"
    assert len(parsed.relationships) == 1
    assert parsed.relationships[0].relationship_type == "saved_list_contains_item"


def test_my_maps_semantics_are_implemented_but_synthetic_only(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260909T012526Z-001.zip",
        {
            "Takeout/My Maps/Synthetic Map.kml": (
                "<kml xmlns='http://www.opengis.net/kml/2.2'><Document>"
                "<Placemark><name>Synthetic</name><Point>"
                "<coordinates>-86.0,39.0</coordinates></Point></Placemark>"
                "</Document></kml>"
            )
        },
    )

    adapter, parsed = _parsed_member(source, "Synthetic Map.kml")

    assert adapter.adapter_name == "geo_semantic"
    assert [record.record_type for record in parsed.records] == ["map", "place"]
    assert parsed.relationships[0].relationship_type == "map_contains_place"


def test_pr6_semantic_parser_versions_force_upgrade_from_generic_identity(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260909T012526Z-001.zip",
        {"Takeout/Maps/places.json": '[{"id":"place-1","name":"Synthetic"}]'},
    )

    adapter, parsed = _parsed_member(source, "places.json")

    assert adapter.adapter_name == "geo_semantic"
    assert parsed.parser_version != "google-generic-structured-v1"
    assert parsed.records[0].record_type == "place"
    assert parsed.records[0].metadata["_pdl"]["semantic_adapter"] == "geo_semantic"
