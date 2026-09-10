from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

import pytest

from personal_data_lab.fingerprints import event_fingerprint
from personal_data_lab.google_importer import GoogleExportError, parse_google_export


def _activity(
    title: str = "Watched A useful video",
    *,
    time: str = "2026-09-01T12:34:56.789Z",
) -> dict:
    return {
        "header": "YouTube",
        "title": title,
        "titleUrl": "https://youtu.be/example",
        "subtitles": [{"name": "Example Channel", "url": "https://youtube.com/example"}],
        "time": time,
        "products": ["YouTube"],
        "activityControls": ["YouTube watch history"],
    }


def test_parses_standalone_my_activity_json(tmp_path: Path) -> None:
    source = tmp_path / "MyActivity.json"
    source.write_text(json.dumps([_activity()]), encoding="utf-8")

    (parsed,) = parse_google_export(source)
    event = parsed.events[0]

    assert parsed.source == "google"
    assert parsed.parser_version == "google-my-activity-v1"
    assert parsed.category == "my_activity/generic/json"
    assert parsed.snapshot_scope == "account/activity"
    assert parsed.event_count == 1
    assert event.event_type == "youtube_activity"
    assert event.source_record_namespace == "activity"
    assert event.actor_identity == "account_owner"
    assert event.occurred_at.isoformat() == "2026-09-01T12:34:56.789000+00:00"
    assert event.text == "Watched A useful video\nExample Channel\nhttps://youtube.com/example"
    assert event.metadata["google"]["titleUrl"] == "https://youtu.be/example"
    assert "time" not in event.metadata["google"]
    assert event.source_record_id.startswith("activity:")
    assert parsed.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert parsed.size_bytes == source.stat().st_size


def test_streams_activity_json_larger_than_the_read_buffer(tmp_path: Path) -> None:
    source = tmp_path / "MyActivity.json"
    records = [_activity(title="x" * 1_200_000), _activity(title="second")]
    source.write_text(json.dumps(records), encoding="utf-8")

    (parsed,) = parse_google_export(source)

    assert parsed.event_count == 2
    assert parsed.events[1].text.startswith("second")


def test_parses_supported_members_from_zip_without_extracting_media(tmp_path: Path) -> None:
    source = tmp_path / "takeout-20260901T123456Z-001.zip"
    with ZipFile(source, "w") as archive:
        archive.writestr(
            "Takeout/My Activity/YouTube/MyActivity.json",
            json.dumps([_activity()]),
        )
        archive.writestr(
            "Takeout/My Activity/Search/MyActivity.json",
            json.dumps(
                [
                    {
                        "header": "example.com",
                        "title": "Searched for streaming JSON",
                        "time": "2026-09-02T00:00:00Z",
                        "products": ["Search"],
                    }
                ]
            ),
        )
        archive.writestr("Takeout/Google Photos/2026/large-video.mp4", b"not-read")

    parsed = parse_google_export(source)

    assert [item.filename for item in parsed] == [
        "Takeout/My Activity/Search/MyActivity.json",
        "Takeout/My Activity/YouTube/MyActivity.json",
    ]
    assert [item.events[0].event_type for item in parsed] == [
        "search_activity",
        "youtube_activity",
    ]
    assert [item.category for item in parsed] == [
        "my_activity/search/json",
        "my_activity/youtube/json",
    ]
    assert all(item.export_id == "20260901T123456Z" for item in parsed)
    assert all(item.exported_at.isoformat() == "2026-09-01T12:34:56+00:00" for item in parsed)
    assert all("large-video.mp4" not in item.original_path for item in parsed)


def test_parses_directory_of_split_takeout_archives(tmp_path: Path) -> None:
    with ZipFile(tmp_path / "takeout-20260901T123456Z-000.zip", "w") as archive:
        archive.writestr("Takeout/Google Photos/large-video.mp4", b"not-read")
    for number, product in ((2, "YouTube"), (1, "Maps")):
        archive_path = tmp_path / f"takeout-20260901T123456Z-{number:03}.zip"
        with ZipFile(archive_path, "w") as archive:
            archive.writestr(
                f"Takeout/My Activity/{product}/MyActivity.json",
                json.dumps(
                    [
                        {
                            "header": product,
                            "title": f"{product} activity",
                            "time": "2026-09-01T00:00:00Z",
                            "products": [product],
                        }
                    ]
                ),
            )

    parsed = parse_google_export(tmp_path)

    assert len(parsed) == 2
    assert [item.events[0].event_type for item in parsed] == [
        "maps_activity",
        "youtube_activity",
    ]


def test_parses_extracted_takeout_tree(tmp_path: Path) -> None:
    source = tmp_path / "Takeout" / "My Activity" / "Maps" / "MyActivity.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps(
            [
                {
                    "header": "Maps",
                    "title": "Searched for coffee",
                    "time": "2026-09-01T00:00:00Z",
                    "products": ["Maps"],
                }
            ]
        ),
        encoding="utf-8",
    )

    (parsed,) = parse_google_export(tmp_path)

    assert parsed.filename == "Takeout/My Activity/Maps/MyActivity.json"
    assert parsed.events[0].event_type == "maps_activity"
    assert parsed.original_path == str(source.resolve())


def test_parses_html_fallback_and_preserves_links(tmp_path: Path) -> None:
    source = tmp_path / "MyActivity.html"
    source.write_text(
        """
        <html><body>
          <div class="outer-cell mdl-cell">
            <div class="header-cell">YouTube</div>
            <div class="content-cell">Watched <a href="https://youtu.be/abc">A video</a>
              <br>Sep 1, 2026
            </div>
          </div>
        </body></html>
        """,
        encoding="utf-8",
    )

    (parsed,) = parse_google_export(source)
    event = parsed.events[0]

    assert event.occurred_at is None
    assert event.event_type == "youtube_activity"
    assert "Watched A video" in event.text
    assert event.metadata["google"]["titleUrl"] == "https://youtu.be/abc"


def test_identical_duplicate_records_get_deterministic_distinct_ids(tmp_path: Path) -> None:
    source = tmp_path / "MyActivity.json"
    source.write_text(json.dumps([_activity(), _activity()]), encoding="utf-8")

    first = parse_google_export(source)[0].events
    second = parse_google_export(source)[0].events

    assert first[0].source_record_id == second[0].source_record_id
    assert first[1].source_record_id == second[1].source_record_id
    assert first[0].source_record_id != first[1].source_record_id
    assert first[1].source_record_id.endswith(":2")


def test_preserves_unparsed_time_in_provider_metadata(tmp_path: Path) -> None:
    source = tmp_path / "MyActivity.json"
    source.write_text(json.dumps([_activity(time="sometime-ish")]), encoding="utf-8")

    event = parse_google_export(source)[0].events[0]

    assert event.occurred_at is None
    assert event.metadata["google"]["unparsed_time"] == "sometime-ish"


def test_rejects_invalid_or_unsupported_sources(tmp_path: Path) -> None:
    invalid = tmp_path / "MyActivity.json"
    invalid.write_text("{}", encoding="utf-8")
    with pytest.raises(GoogleExportError, match="top-level array"):
        parse_google_export(invalid)

    unsupported = tmp_path / "Takeout"
    unsupported.mkdir()
    (unsupported / "archive_browser.html").write_text("<html></html>", encoding="utf-8")
    with pytest.raises(GoogleExportError, match="no supported Google activity files"):
        parse_google_export(tmp_path)


def test_parses_directly_selected_activity_directory_and_utf8_bom(tmp_path: Path) -> None:
    activity_directory = tmp_path / "My Activity"
    source = activity_directory / "Search" / "My_Activity.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"\xef\xbb\xbf" + json.dumps([_activity()]).encode())

    (parsed,) = parse_google_export(activity_directory)

    assert parsed.filename == "Search/My_Activity.json"
    assert parsed.category == "my_activity/search/json"
    assert parsed.event_count == 1


def test_filename_and_path_changes_do_not_change_parsing_category(tmp_path: Path) -> None:
    payload = json.dumps([_activity()])
    first = tmp_path / "first" / "My Activity" / "Search" / "MyActivity.json"
    second = tmp_path / "second" / "My Activity" / "Search" / "My_Activity.json"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text(payload, encoding="utf-8")
    second.write_text(payload, encoding="utf-8")

    first_parsed = parse_google_export(first.parent.parent)[0]
    second_parsed = parse_google_export(second.parent.parent)[0]

    assert first_parsed.sha256 == second_parsed.sha256
    assert first_parsed.category == second_parsed.category == "my_activity/search/json"


@pytest.mark.parametrize(
    ("raw_time", "expected"),
    [
        ("2026-09-01T07:34:56-05:00", "2026-09-01T12:34:56+00:00"),
        (1_788_266_096, "2026-09-01T12:34:56+00:00"),
        (1_788_266_096_000, "2026-09-01T12:34:56+00:00"),
        ("2026-09-01T12:34:56", "2026-09-01T12:34:56+00:00"),
    ],
)
def test_normalizes_supported_timestamp_forms_to_utc(
    tmp_path: Path,
    raw_time: object,
    expected: str,
) -> None:
    source = tmp_path / "MyActivity.json"
    record = _activity()
    record["time"] = raw_time
    source.write_text(json.dumps([record]), encoding="utf-8")

    event = parse_google_export(source)[0].events[0]

    assert event.occurred_at is not None
    assert event.occurred_at.isoformat() == expected


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b'[{"title":"one"} {"title":"two"}]', "missing a comma"),
        (b'[{"title":"one"}', "ended unexpectedly"),
        (b'[] trailing', "content after the top-level array"),
        (b'["not an object"]', "must be an object"),
        (b'[{"unknown":true}]', "has no recognized fields"),
        (b'["\xff"]', "could not normalize"),
        (b'[{"title":"invalid number","time":NaN}]', "could not normalize"),
    ],
)
def test_rejects_malformed_json_without_skipping_records(
    tmp_path: Path,
    payload: bytes,
    message: str,
) -> None:
    source = tmp_path / "MyActivity.json"
    source.write_bytes(payload)

    with pytest.raises(GoogleExportError, match=message):
        parse_google_export(source)


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ("YouTube", "youtube_activity"),
        ("Maps", "maps_activity"),
        ("Search", "search_activity"),
        ("Chrome", "browser_activity"),
        ("Shopping", "shopping_activity"),
        ("Google Play", "play_activity"),
        ("My Ad Center", "ad_activity"),
        ("Unclassified Product", "google_activity"),
    ],
)
def test_classifies_supported_products(
    tmp_path: Path,
    evidence: str,
    expected: str,
) -> None:
    source = tmp_path / "MyActivity.json"
    source.write_text(
        json.dumps([{"title": "Activity", "header": evidence}]),
        encoding="utf-8",
    )

    event = parse_google_export(source)[0].events[0]

    assert event.event_type == expected


def test_flattens_text_and_retains_unrecognized_provider_fields(tmp_path: Path) -> None:
    source = tmp_path / "MyActivity.json"
    source.write_text(
        json.dumps(
            [
                {
                    "title": "  Repeated   value ",
                    "subtitles": [
                        {"name": "Repeated value", "url": "https://example.invalid"}
                    ],
                    "description": ["Detail", {"nested": "More   detail"}],
                    "time": "2026-09-01T00:00:00Z",
                    "futureProviderField": {"kept": True},
                }
            ]
        ),
        encoding="utf-8",
    )

    event = parse_google_export(source)[0].events[0]

    assert event.text == (
        "Repeated value\nhttps://example.invalid\nDetail\nMore detail"
    )
    assert event.metadata["google"]["futureProviderField"] == {"kept": True}
    assert "time" not in event.metadata["google"]


def test_identity_ignores_json_object_key_order(tmp_path: Path) -> None:
    first_path = tmp_path / "first" / "MyActivity.json"
    second_path = tmp_path / "second" / "MyActivity.json"
    first_path.parent.mkdir()
    second_path.parent.mkdir()
    first_path.write_text(
        '[{"title":"Activity","header":"Search","time":"2026-09-01T00:00:00Z"}]',
        encoding="utf-8",
    )
    second_path.write_text(
        '[{"time":"2026-09-01T00:00:00Z","header":"Search","title":"Activity"}]',
        encoding="utf-8",
    )

    first = parse_google_export(first_path)[0].events[0]
    second = parse_google_export(second_path)[0].events[0]

    assert first.source_record_id == second.source_record_id
    assert event_fingerprint(first) == event_fingerprint(second)


def test_export_authority_does_not_change_event_content(tmp_path: Path) -> None:
    record = json.dumps([_activity()])
    first_path = tmp_path / "takeout-20260901T000000Z-001.zip"
    second_path = tmp_path / "takeout-20260902T000000Z-001.zip"
    member = "Takeout/My Activity/YouTube/MyActivity.json"
    for path in (first_path, second_path):
        with ZipFile(path, "w") as archive:
            archive.writestr(member, record)

    first = parse_google_export(first_path)[0].events[0]
    second = parse_google_export(second_path)[0].events[0]

    assert first.authority_at == datetime(2026, 9, 1, tzinfo=UTC)
    assert second.authority_at == datetime(2026, 9, 2, tzinfo=UTC)
    assert first.source_record_id == second.source_record_id
    assert event_fingerprint(first) == event_fingerprint(second)


def test_html_multiple_cards_deduplicates_links_and_rejects_empty_cards(
    tmp_path: Path,
) -> None:
    source = tmp_path / "MyActivity.html"
    source.write_text(
        """
        <div class="outer-cell"><div>YouTube</div><div>
          Watched <a href="https://example.invalid/a">A</a>
          <a href="https://example.invalid/a">again</a>
        </div></div>
        <div class="outer-cell">Second card</div>
        """,
        encoding="utf-8",
    )

    parsed = parse_google_export(source)[0]

    assert parsed.category == "my_activity/generic/html"
    assert parsed.event_count == 2
    assert parsed.events[0].metadata["google"]["links"] == [
        "https://example.invalid/a"
    ]
    assert all(event.occurred_at is None for event in parsed.events)

    source.write_text("<html><body>No cards</body></html>", encoding="utf-8")
    with pytest.raises(GoogleExportError, match="no recognizable activity cards"):
        parse_google_export(source)


def test_html_one_line_card_uses_product_directory_fallback(tmp_path: Path) -> None:
    activity_root = tmp_path / "My Activity"
    source = activity_root / "Maps" / "MyActivity.html"
    source.parent.mkdir(parents=True)
    source.write_text(
        '<div class="outer-cell">Opened a saved place</div>',
        encoding="utf-8",
    )

    parsed = parse_google_export(activity_root)[0]
    event = parsed.events[0]

    assert parsed.category == "my_activity/maps/html"
    assert event.event_type == "maps_activity"
    assert event.text == "Opened a saved place"
    assert event.metadata["google"]["takeout_product"] == "Maps"


def test_rejects_invalid_zip_and_invalid_takeout_timestamp(tmp_path: Path) -> None:
    invalid_zip = tmp_path / "takeout-20260901T000000Z.zip"
    invalid_zip.write_bytes(b"not a zip")
    with pytest.raises(GoogleExportError, match="invalid ZIP"):
        parse_google_export(invalid_zip)

    invalid_timestamp = tmp_path / "takeout-20269999T999999Z.zip"
    with ZipFile(invalid_timestamp, "w") as archive:
        archive.writestr("Takeout/My Activity/Search/MyActivity.json", "[]")
    (parsed,) = parse_google_export(invalid_timestamp)
    assert parsed.exported_at is None
    assert parsed.export_id is None
