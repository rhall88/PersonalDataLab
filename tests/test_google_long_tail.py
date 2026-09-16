from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from personal_data_lab.google_takeout.adapters import (
    DEFAULT_ADAPTER_REGISTRY,
    open_takeout_member,
)
from personal_data_lab.google_takeout.inventory import inspect_google
from personal_data_lab.google_takeout.long_tail import LONG_TAIL_PRODUCTS


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


def test_pr11_exact_long_tail_contract_is_registered() -> None:
    assert len(LONG_TAIL_PRODUCTS) == 25
    assert len(set(LONG_TAIL_PRODUCTS)) == 25
    assert {adapter.adapter_name for adapter in DEFAULT_ADAPTER_REGISTRY.adapters} >= {
        "long_tail_semantic",
        "long_tail_asset",
        "my_activity_asset",
    }


def test_access_log_rows_are_events_with_provider_fields_and_time(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Access Log Activity/activity.csv": (
                "Gaia ID\tActivity Timestamp\tIP Address\tActivity Type\n"
                "gaia-1\t2026-09-16T12:00:00Z\t192.0.2.1\tLogin\n"
            )
        },
    )

    adapter, parsed = _parse(source, "activity.csv")

    assert adapter.adapter_name == "long_tail_semantic"
    assert parsed.events[0].event_type == "account_access"
    assert parsed.events[0].occurred_at is not None
    assert parsed.events[0].metadata["google"]["IP Address"] == "192.0.2.1"


def test_chrome_expands_devices_and_bookmarks_without_losing_fields(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Chrome/device.json": json.dumps(
                {
                    "Device Info": [
                        {
                            "cache_guid": "device-1",
                            "client_name": "Synthetic browser",
                            "future": {"preserved": True},
                        }
                    ]
                }
            ),
            "Takeout/Chrome/bookmarks.html": (
                '<!DOCTYPE NETSCAPE-Bookmark-file-1><a href="https://example.invalid/">'
                "Synthetic bookmark</a>"
            ),
        },
    )

    _, device = _parse(source, "device.json")
    _, bookmarks = _parse(source, "bookmarks.html")

    assert device.records[0].record_type == "device"
    assert device.records[0].metadata["google"]["value"]["future"] == {"preserved": True}
    assert bookmarks.records[0].record_type == "saved_item"
    assert bookmarks.records[0].metadata["google"]["url"] == "https://example.invalid/"


def test_youtube_types_provider_rows_and_streams_media_as_external_asset(
    tmp_path: Path,
) -> None:
    video = b"synthetic-video"
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/YouTube and YouTube Music/subscriptions/subscriptions.csv": (
                "Channel Id,Channel Url,Channel Title\n"
                "channel-1,https://youtube.invalid/channel-1,Synthetic channel\n"
            ),
            "Takeout/YouTube and YouTube Music/comments/comments.csv": (
                "Comment ID,Channel ID,Comment Create Timestamp,Comment Text\n"
                "comment-1,channel-1,2026-09-16T12:00:00Z,Synthetic comment\n"
            ),
            "Takeout/YouTube and YouTube Music/live chat/messages.csv": (
                "Message ID,Sender Channel ID,Message Create Timestamp (UTC),Conversation ID\n"
                "message-1,channel-1,2026-09-16T12:01:00Z,conversation-1\n"
            ),
            "Takeout/YouTube and YouTube Music/videos/video.mp4": video,
        },
    )

    _, subscriptions = _parse(source, "subscriptions.csv")
    _, comments = _parse(source, "comments.csv")
    _, messages = _parse(source, "messages.csv")
    asset_adapter, media = _parse(source, "video.mp4")

    assert subscriptions.records[0].record_type == "subscription"
    assert comments.records[0].record_type == "contribution"
    assert comments.records[0].source_record_id.startswith("provider:comment-1:")
    assert messages.events[0].event_type == "youtube_chat_message"
    assert asset_adapter.adapter_name == "long_tail_asset"
    assert media.assets[0].sha256 == hashlib.sha256(video).hexdigest()


def test_feedback_surveys_notifications_and_empty_exports_are_explicit(
    tmp_path: Path,
) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Google Feedback/feedback.json": json.dumps(
                {
                    "description": "Synthetic feedback",
                    "product_name": "Synthetic product",
                    "submission_time": "2026-09-16T12:00:00Z",
                }
            ),
            "Takeout/Google Product Surveys/survey.json": json.dumps(
                {
                    "surveyTitle": "Synthetic survey",
                    "promptTime": "2026-09-16T12:01:00Z",
                    "responses": "Synthetic answer",
                }
            ),
            "Takeout/Search Notifications/subscriptions.csv": (
                "SUBSCRIPTION IDENTIFIER\tSUBSCRIPTION STATUS\nsynthetic\tACTIVE\n"
            ),
            "Takeout/Alerts/alerts.json": "{}",
            "Takeout/News/empty.txt": "",
        },
    )

    _, feedback = _parse(source, "feedback.json")
    _, surveys = _parse(source, "survey.json")
    _, notifications = _parse(source, "subscriptions.csv")
    _, alerts = _parse(source, "alerts.json")
    _, news = _parse(source, "empty.txt")

    assert feedback.records[0].record_type == "survey_response"
    assert surveys.records[0].record_type == "survey_response"
    assert notifications.records[0].record_type == "subscription"
    assert alerts.record_count == alerts.event_count == 0
    assert news.record_count == news.event_count == 0


def test_absent_product_contracts_cover_education_geo_health_legal_and_publishing(
    tmp_path: Path,
) -> None:
    appeal = b"%PDF-synthetic-appeal"
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Assignments/assignments.json": json.dumps(
                [{"id": "assignment-1", "title": "Synthetic assignment"}]
            ),
            "Takeout/Classroom/courses.csv": "id,name\ncourse-1,Synthetic course\n",
            "Takeout/Google Earth/map.kml": (
                '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
                "<name>Synthetic map</name></Document></kml>"
            ),
            "Takeout/Google Health/measurements.json": json.dumps(
                [{"id": "health-1", "value": 1, "unit": "synthetic-unit"}]
            ),
            "Takeout/Blogger/blog.atom": (
                '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
                "<id>post-1</id><title>Synthetic post</title></entry></feed>"
            ),
            "Takeout/Content returned from your appeal/appeal.pdf": appeal,
        },
    )

    _, assignments = _parse(source, "assignments.json")
    _, classroom = _parse(source, "courses.csv")
    _, earth = _parse(source, "map.kml")
    _, health = _parse(source, "measurements.json")
    _, blogger = _parse(source, "blog.atom")
    _, legal = _parse(source, "appeal.pdf")

    assert assignments.records[0].record_type == "assignment"
    assert classroom.records[0].record_type == "classroom_course"
    assert earth.records[0].record_type in {"map", "place"}
    assert health.records[0].record_type == "health_record"
    assert blogger.records[0].record_type == "contribution"
    assert legal.assets[0].sha256 == hashlib.sha256(appeal).hexdigest()


def test_extensionless_marketplace_and_tenor_favorites_are_semantic(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Google Workspace Marketplace/installed-applications": (
                "Synthetic application\nStatus: installed\n"
            ),
            "Takeout/Tenor/favorites.json": json.dumps(
                {
                    "id": "pack-1",
                    "pack_name": "Synthetic pack",
                    "favorited_uploads": ["gif-1", "gif-2"],
                }
            ),
        },
    )

    _, marketplace = _parse(source, "installed-applications")
    _, tenor = _parse(source, "favorites.json")

    assert marketplace.records[0].record_type == "subscription"
    assert [record.record_type for record in tenor.records] == [
        "saved_item",
        "saved_item",
        "saved_item",
    ]


def test_nested_youtube_my_activity_keeps_existing_streaming_route(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/YouTube and YouTube Music/My Activity/My Activity.html": (
                "<html><body><div class='mdl-grid'><div>Watched synthetic video</div>"
                "<div>Sep 16, 2026, 12:00:00 PM UTC</div></div></body></html>"
            )
        },
    )

    member = _member(source, "My Activity.html")
    adapter = DEFAULT_ADAPTER_REGISTRY.resolve(member)

    assert adapter is not None
    assert adapter.adapter_name == "my_activity"


def test_top_level_my_activity_attachments_are_external_assets(tmp_path: Path) -> None:
    attachment = b"synthetic-activity-attachment"
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {"Takeout/My Activity/Assistant/attachment.png": attachment},
    )

    adapter, parsed = _parse(source, "attachment.png")

    assert adapter.adapter_name == "my_activity_asset"
    assert parsed.assets[0].sha256 == hashlib.sha256(attachment).hexdigest()
