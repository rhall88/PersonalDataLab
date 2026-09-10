from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import pytest

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
    assert len(parsed.sha256) == 64


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
