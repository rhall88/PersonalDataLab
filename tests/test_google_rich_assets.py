from __future__ import annotations

import hashlib
import io
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


def _docx(text: str) -> bytes:
    target = io.BytesIO()
    with ZipFile(target, "w") as archive:
        archive.writestr(
            "word/document.xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="urn:synthetic"><w:body>'
                f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
                "</w:body></w:document>"
            ),
        )
    return target.getvalue()


def test_photos_sidecar_links_to_the_same_external_asset_identity(tmp_path: Path) -> None:
    media = b"synthetic-photo"
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Google Photos/Album/photo.jpg.json": json.dumps(
                {
                    "title": "photo.jpg",
                    "description": "Synthetic photo",
                    "photoTakenTime": {
                        "timestamp": "1789560000",
                        "formatted": "synthetic display value",
                    },
                    "geoData": {"latitude": 1.25, "longitude": -2.5},
                    "future": {"preserved": True},
                }
            ),
            "Takeout/Google Photos/Album/photo.jpg": media,
        },
    )

    sidecar_adapter, sidecar = _parse(source, "photo.jpg.json")
    asset_adapter, binary = _parse(source, "photo.jpg")

    assert sidecar_adapter.adapter_name == "rich_asset_semantic"
    assert sidecar.parser_version == "google-photos-v2"
    assert asset_adapter.adapter_name == "rich_binary_asset"
    assert sidecar.records[0].record_type == "photo_metadata"
    assert sidecar.records[0].metadata["google"]["future"] == {"preserved": True}
    assert sidecar.relationships[0].object_source_id == binary.assets[0].source_asset_id
    assert binary.assets[0].sha256 == hashlib.sha256(media).hexdigest()


def test_photos_duplicate_sidecars_remain_distinct_observations(tmp_path: Path) -> None:
    first = {
        "title": "photo.jpg",
        "photoTakenTime": {"timestamp": "1789560000"},
        "url": "https://example.invalid/first",
    }
    second = {
        "title": "photo.jpg",
        "photoTakenTime": {"timestamp": "1789560001"},
        "url": "https://example.invalid/second",
    }
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Google Photos/Album/photo.jpg.supplemental-metadata.json": json.dumps(first),
            "Takeout/Google Photos/Album/photo.jpg.supplemental-metadata(1).json": json.dumps(second),
        },
    )

    _, parsed_first = _parse(source, "photo.jpg.supplemental-metadata.json")
    _, parsed_second = _parse(source, "photo.jpg.supplemental-metadata(1).json")

    assert parsed_first.records[0].source_record_id != parsed_second.records[0].source_record_id
    assert (
        parsed_first.relationships[0].object_source_id
        == parsed_second.relationships[0].object_source_id
    )


def test_photos_album_entries_create_ordered_asset_relationships(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Google Photos/Album/metadata.json": json.dumps(
                {
                    "title": "Synthetic album",
                    "entries": [{"text": "one.jpg"}, {"text": "two.jpg"}],
                }
            )
        },
    )

    _, parsed = _parse(source, "metadata.json")

    assert parsed.records[0].record_type == "photo_album"
    assert [item.metadata["_pdl"]["source_order"] for item in parsed.relationships] == [0, 1]


def test_drive_extracts_ooxml_text_and_indexes_the_source_asset(tmp_path: Path) -> None:
    content = _docx("Synthetic document text")
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {"Takeout/Drive/folder/document.docx": content},
    )

    adapter, parsed = _parse(source, "document.docx")

    assert adapter.adapter_name == "rich_asset_semantic"
    assert parsed.records[0].record_type == "document"
    assert "Synthetic document text" in (parsed.records[0].text or "")
    assert parsed.assets[0].sha256 == hashlib.sha256(content).hexdigest()
    assert parsed.relationships[0].object_source_id == parsed.assets[0].source_asset_id


def test_drive_tsv_disguised_as_csv_is_structured_without_losing_fields(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Drive/table.csv": (
                "Entity Name\tSetting Value\nSynthetic setting\tEnabled\n"
            )
        },
    )

    _, parsed = _parse(source, "table.csv")

    assert parsed.record_count == parsed.relationship_count == 1
    assert parsed.records[0].metadata["google"] == {
        "Entity Name": "Synthetic setting",
        "Setting Value": "Enabled",
    }


def test_recorder_transcript_links_to_audio_and_absent_products_are_implemented(
    tmp_path: Path,
) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Recorder/session.txt": "Synthetic transcript",
            "Takeout/Recorder/session.m4a": b"synthetic-audio",
            "Takeout/Phone Audio/call.wav": b"synthetic-phone-audio",
            "Takeout/Street View/image.jpg": b"synthetic-street-view",
        },
    )

    transcript_adapter, transcript = _parse(source, "session.txt")
    audio_adapter, audio = _parse(source, "session.m4a")
    phone_adapter, phone = _parse(source, "call.wav")
    street_adapter, street = _parse(source, "image.jpg")

    assert transcript_adapter.adapter_name == "rich_asset_semantic"
    assert {audio_adapter.adapter_name, phone_adapter.adapter_name, street_adapter.adapter_name} == {
        "rich_binary_asset"
    }
    assert transcript.records[0].record_type == "transcript"
    assert transcript.relationships[0].object_source_id == audio.assets[0].source_asset_id
    assert phone.asset_count == street.asset_count == 1


def test_nested_my_activity_in_drive_routes_without_adapter_ambiguity(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Drive/archive/Takeout/My Activity/Google Pay/MyActivity.html": (
                "<html><body><div class='mdl-grid'><div>Visited synthetic entry</div>"
                "<div>Sep 16, 2026, 12:00:00 PM UTC</div></div></body></html>"
            )
        },
    )

    member = _member(source, "MyActivity.html")
    adapter = DEFAULT_ADAPTER_REGISTRY.resolve(member)

    assert adapter is not None
    assert adapter.adapter_name == "my_activity"
