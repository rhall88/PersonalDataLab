from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

import pytest

from personal_data_lab import cli
from personal_data_lab.facebook_export import orchestrator
from personal_data_lab.facebook_export.adapters import (
    ADAPTERS,
    FacebookAdapterError,
    _fix_mojibake,
    _json_safe,
)
from personal_data_lab.facebook_export.inventory import (
    FacebookInventoryError,
    inspect_facebook,
    open_facebook_member,
    render_inventory_table,
)
from personal_data_lab.facebook_export.products import PRODUCT_REGISTRY
from personal_data_lab.models import ImportResult


def _zip(path: Path, members: list[tuple[str, str | bytes]]) -> Path:
    with ZipFile(path, "w") as archive:
        for name, content in members:
            archive.writestr(name, content)
    return path


def _parsed(member):
    adapter = ADAPTERS[member.candidate_adapter]
    with open_facebook_member(member) as stream:
        return adapter.parse(stream, member)


def test_registry_has_stable_observed_product_contract() -> None:
    assert len(PRODUCT_REGISTRY) == 74
    assert PRODUCT_REGISTRY["messages"].display_name == "Messages"
    assert PRODUCT_REGISTRY["friends"].root == "connections"
    assert PRODUCT_REGISTRY["files"].display_name == "Other Files"


def test_adapter_contract_separates_representation_from_interpretation() -> None:
    structured = ADAPTERS["facebook_structured"]
    assert structured.support_level == "losslessly_structured"
    assert structured.interpretation_level == "generic_lossless"
    assert {
        adapter.interpretation_level
        for name, adapter in ADAPTERS.items()
        if name != "facebook_structured"
    } == {"provider_specific"}


def test_inventory_accounts_for_json_and_asset_members(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "facebook-export.zip",
        [
            ("connections/friends/your_friends.json", '{"friends_v2": [{"name": "Ada"}]}'),
            (
                "your_facebook_activity/messages/inbox/ada_123/message_1.json",
                json.dumps(
                    {
                        "participants": [{"name": "Ada"}],
                        "messages": [
                            {"sender_name": "Ada", "timestamp_ms": 1700000000000, "content": "hi"}
                        ],
                        "title": "Ada",
                        "thread_path": "inbox/ada_123",
                        "is_still_participant": True,
                    }
                ),
            ),
            ("your_facebook_activity/messages/inbox/ada_123/photos/1.jpg", b"jpegbytes"),
        ],
    )
    inventory = inspect_facebook(source)

    assert inventory.recursive_entries == 3
    assert inventory.classified_data_members == 3
    assert inventory.unknown_members_count == 0
    assert inventory.accounted_members == inventory.recursive_entries
    assert inventory.physical_source_bytes == source.stat().st_size
    assert inventory.logical_leaf_bytes == sum(
        len(item) if isinstance(item, bytes) else len(item.encode())
        for _, item in [
            ("", '{"friends_v2": [{"name": "Ada"}]}'),
            ("", "x" * 0),
        ]
    ) + len(b"jpegbytes") + len(
        json.dumps(
            {
                "participants": [{"name": "Ada"}],
                "messages": [
                    {"sender_name": "Ada", "timestamp_ms": 1700000000000, "content": "hi"}
                ],
                "title": "Ada",
                "thread_path": "inbox/ada_123",
                "is_still_participant": True,
            }
        ).encode()
    )
    assert "Facebook Export Inventory" in render_inventory_table(inventory)
    products = {item.product_key: item for item in inventory.products}
    assert products["friends"].adapters == ("facebook_structured",)
    assert products["messages"].adapters == ("facebook_asset", "facebook_messages")


def test_inventory_rejects_invalid_zip_and_marks_unregistered_path_unknown(tmp_path: Path) -> None:
    invalid = tmp_path / "broken.zip"
    invalid.write_bytes(b"not a zip")
    with pytest.raises(FacebookInventoryError, match="invalid or unreadable ZIP"):
        inspect_facebook(invalid)

    unregistered = _zip(
        tmp_path / "facebook-export.zip",
        [("some_new_root/unknown_category.json", "{}")],
    )
    inventory = inspect_facebook(unregistered)
    assert inventory.unknown_members_count == 1
    assert inventory.unknown_members[0].reason == "unregistered_product_path"


def test_public_dict_redacts_paths_by_default(tmp_path: Path) -> None:
    secret = "PRIVATE THREAD NAME"
    source = _zip(
        tmp_path / "facebook-export.zip",
        [(f"your_facebook_activity/posts/{secret}.json", "[]")],
    )
    inventory = inspect_facebook(source)

    assert secret not in json.dumps(inventory.public_dict())
    assert secret in json.dumps(inventory.public_dict(include_paths=True))


def test_structured_adapter_unwraps_single_key_envelope_and_keeps_mixed_dict_as_one_record(
    tmp_path: Path,
) -> None:
    source = _zip(
        tmp_path / "facebook-export.zip",
        [
            (
                "connections/friends/your_friends.json",
                json.dumps({"friends_v2": [{"name": "Ada", "timestamp": 1700000000}, {"name": "Bea"}]}),
            ),
            (
                "ads_information/ad_preferences.json",
                json.dumps({"timestamp": 1700000000, "media": [], "label_values": [{"label": "x"}]}),
            ),
        ],
    )
    members = {item.filename: item for item in inspect_facebook(source).members}
    friends = _parsed(members["your_friends.json"])
    assert friends.record_count == 2
    assert friends.records[0].title == "Ada"

    prefs = _parsed(members["ad_preferences.json"])
    assert prefs.record_count == 1
    assert prefs.records[0].metadata["facebook"]["label_values"] == [{"label": "x"}]


def test_backdated_life_event_occurs_on_its_event_date_not_its_creation_date(
    tmp_path: Path,
) -> None:
    created = 1505844287  # 2017-09-19: when the profile entry was created on Facebook
    graduated = 1564848000  # 2019-08-03: the date the life event actually happened
    life_event = {
        "timestamp": created,
        "title": "Ryan added a life event from August 3, 2019: Graduated from Ivy Tech.",
        "data": [{"backdated_timestamp": graduated}, {}],
        "attachments": [
            {"data": [{"life_event": {"title": "Graduated from Ivy Tech",
                                      "end_date": {"day": 3, "year": 2019, "month": 8}}}]}
        ],
    }
    ordinary_post = {"timestamp": created, "title": "An ordinary post."}
    source = _zip(
        tmp_path / "facebook-export.zip",
        [
            (
                "your_facebook_activity/posts/your_posts__check_ins__photos_and_videos_1.json",
                json.dumps([life_event, ordinary_post]),
            )
        ],
    )
    (member,) = inspect_facebook(source).members
    parsed = _parsed(member)

    life_record, ordinary_record = parsed.records
    assert life_record.occurred_at == datetime.fromtimestamp(graduated, tz=UTC)
    assert parsed.events[0].occurred_at == datetime.fromtimestamp(graduated, tz=UTC)
    assert life_record.metadata["_pdl"]["occurred_at_basis"] == "backdated_timestamp"
    # The creation time is not lost: the complete provider value is retained.
    assert life_record.metadata["facebook"]["timestamp"] == created

    assert ordinary_record.occurred_at == datetime.fromtimestamp(created, tz=UTC)
    assert "occurred_at_basis" not in ordinary_record.metadata["_pdl"]


def test_structured_parser_version_changes_so_reimports_reprocess(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "facebook-export.zip",
        [("connections/friends/your_friends.json", json.dumps({"friends_v2": [{"name": "Ada"}]}))],
    )
    (member,) = inspect_facebook(source).members
    assert _parsed(member).parser_version == "facebook-export-v1/facebook_structured+backdated-1"


def test_mojibake_and_null_bytes_are_fixed_without_altering_plain_text() -> None:
    mojibake = "Ryan â¤ Facebook"
    assert _fix_mojibake(mojibake) == "Ryan ❤ Facebook"
    assert _fix_mojibake("plain ascii") == "plain ascii"
    assert _json_safe("has\x00nul") == "has\\u0000nul"
    assert _json_safe({"a": ["x\x00y"]}) == {"a": ["x\\u0000y"]}


def test_messages_adapter_builds_conversation_with_events_and_call_type(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "facebook-export.zip",
        [
            (
                "your_facebook_activity/messages/inbox/ada_123/message_1.json",
                json.dumps(
                    {
                        "participants": [{"name": "Ada"}, {"name": "Ryan Hallgarth"}],
                        "messages": [
                            {
                                "sender_name": "Ada",
                                "timestamp_ms": 1700000000000,
                                "content": "hello",
                            },
                            {
                                "sender_name": "Ryan Hallgarth",
                                "timestamp_ms": 1700000005000,
                                "call_duration": 30,
                                "missed": False,
                            },
                        ],
                        "title": "",
                        "thread_path": "inbox/ada_123",
                        "is_still_participant": True,
                    }
                ),
            )
        ],
    )
    member = inspect_facebook(source).members[0]
    parsed = _parsed(member)

    assert parsed.conversation_count == 1
    conversation = parsed.conversations[0]
    assert conversation.title == "Ada, Ryan Hallgarth"
    assert len(conversation.events) == 2
    assert {event.event_type for event in conversation.events} == {"message", "call"}
    assert len({event.source_record_id for event in conversation.events}) == 2


def test_messages_adapter_is_stable_across_multi_part_threads(tmp_path: Path) -> None:
    def _part(messages: list[dict]) -> str:
        return json.dumps(
            {
                "participants": [{"name": "Ada"}],
                "messages": messages,
                "title": "Ada",
                "thread_path": "inbox/ada_123",
                "is_still_participant": True,
            }
        )

    source = _zip(
        tmp_path / "facebook-export.zip",
        [
            (
                "your_facebook_activity/messages/inbox/ada_123/message_1.json",
                _part([{"sender_name": "Ada", "timestamp_ms": 1700000000000, "content": "a"}]),
            ),
            (
                "your_facebook_activity/messages/inbox/ada_123/message_2.json",
                _part([{"sender_name": "Ada", "timestamp_ms": 1600000000000, "content": "b"}]),
            ),
        ],
    )
    members = sorted(inspect_facebook(source).members, key=lambda item: item.filename)
    first, second = (_parsed(item).conversations[0] for item in members)

    assert first.source_conversation_id == second.source_conversation_id
    assert first.title == second.title == "Ada"
    assert first.metadata == second.metadata
    assert first.started_at is None and second.started_at is None


def test_asset_adapter_uses_content_derived_identity_and_links_thread_attachment(
    tmp_path: Path,
) -> None:
    source = _zip(
        tmp_path / "facebook-export.zip",
        [
            ("your_facebook_activity/messages/inbox/ada_123/photos/1.jpg", b"same bytes"),
            ("your_facebook_activity/messages/inbox/bea_456/photos/2.jpg", b"same bytes"),
            ("your_facebook_activity/posts/media/album/3.jpg", b"same bytes"),
        ],
    )
    members = inspect_facebook(source).members
    thread_one, thread_two, post_media = (_parsed(item) for item in members)

    assert (
        thread_one.assets[0].source_asset_id
        == thread_two.assets[0].source_asset_id
        == post_media.assets[0].source_asset_id
    )
    assert thread_one.assets[0].filename == thread_two.assets[0].filename
    assert thread_one.relationship_count == 1
    assert thread_two.relationship_count == 1
    assert thread_one.relationships[0].subject_source_id != thread_two.relationships[0].subject_source_id
    assert post_media.relationship_count == 0


def test_malformed_json_error_never_contains_payload(tmp_path: Path) -> None:
    secret = "DO NOT LOG THIS PAYLOAD"
    source = _zip(
        tmp_path / "facebook-export.zip",
        [("connections/friends/your_friends.json", '{"secret":"' + secret)],
    )
    member = inspect_facebook(source).members[0]
    with open_facebook_member(member) as stream, pytest.raises(FacebookAdapterError) as raised:
        ADAPTERS["facebook_structured"].parse(stream, member)
    assert secret not in str(raised.value)


def test_orchestrator_full_coverage_is_production_ready(tmp_path: Path, monkeypatch) -> None:
    source = _zip(
        tmp_path / "facebook-export.zip",
        [("connections/friends/your_friends.json", '{"friends_v2": [{"name": "Ada"}]}')],
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
                source_relationships=parsed.relationship_count,
                database_relationships=parsed.relationship_count,
                short_circuited=True,
            ),
        )

    monkeypatch.setattr(orchestrator, "import_parsed_exports", imported)
    summary = orchestrator.import_facebook_all(object(), str(source))
    assert summary.members_imported == summary.members_short_circuited == 1
    assert summary.complete_accounting
    assert summary.production_ready
    assert summary.exit_code == 0
    assert summary.coverage["friends"].status == "losslessly_structured"
    assert summary.coverage["friends"].interpretation_status == "generic_lossless"
    assert summary.coverage["messages"].status == "not_present_in_export"
    assert summary.coverage["messages"].interpretation_status == "not_present_in_export"


def test_facebook_cli_defaults_are_private_and_machine_readable(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    secret = "PRIVATE POST TITLE"
    source = _zip(
        tmp_path / "facebook-export.zip",
        [(f"your_facebook_activity/posts/{secret}.json", "[]")],
    )
    assert cli.main(["inspect-facebook", str(source)]) == 0
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
    monkeypatch.setattr(
        cli, "database_url", lambda override: "postgresql://user:pass@localhost/ignored"
    )
    monkeypatch.setattr(cli, "create_database_engine", lambda url: object())
    assert cli.main(["import-facebook-all", str(source)]) == 0
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
