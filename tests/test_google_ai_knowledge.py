from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
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


def test_flow_projects_generated_media_and_assets(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Flow/projects.json": json.dumps(
                {
                    "projects": [
                        {
                            "projectId": "project-1",
                            "name": "Synthetic project",
                            "createdAt": "2026-09-16T12:00:00Z",
                        }
                    ]
                }
            ),
            "Takeout/Flow/media.json": json.dumps(
                {
                    "mediaEntries": [
                        {
                            "mediaId": "media-1",
                            "projectId": "project-1",
                            "createdAt": "2026-09-16T12:01:00Z",
                            "generationInputs": {
                                "prompt": "Synthetic prompt",
                                "aspectRatio": "1:1",
                            },
                            "future": "preserved",
                        }
                    ]
                }
            ),
            "Takeout/Flow/media-1.jpg": b"synthetic-image",
        },
    )

    adapter, projects = _parse(source, "projects.json")
    _, media = _parse(source, "media.json")
    asset_adapter, asset = _parse(source, "media-1.jpg")

    assert adapter.adapter_name == "ai_knowledge_semantic"
    assert asset_adapter.adapter_name == "ai_knowledge_asset"
    assert projects.records[0].record_type == "ai_project"
    assert media.records[0].record_type == "generated_media"
    assert "Synthetic prompt" in (media.records[0].text or "")
    assert media.records[0].metadata["google"]["future"] == "preserved"
    assert media.relationship_count == 1
    assert asset.assets[0].sha256 == hashlib.sha256(b"synthetic-image").hexdigest()


def test_keep_and_journal_preserve_note_semantics_and_provider_times(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Keep/note.json": json.dumps(
                {
                    "title": "Synthetic Keep note",
                    "textContent": "Note body",
                    "createdTimestampUsec": 1789560000000000,
                    "userEditedTimestampUsec": 1789560060000000,
                    "isPinned": True,
                    "isArchived": False,
                    "isTrashed": False,
                }
            ),
            "Takeout/Journal/journal.json": json.dumps(
                {
                    "journal_content": {
                        "journalContentEntity": {
                            "title": "Synthetic journal entry",
                            "content": "Journal body",
                            "createdTime": {"seconds": 1789560000, "nanos": 500000000},
                            "modifiedTime": {"seconds": 1789560060, "nanos": 0},
                        }
                    }
                }
            ),
        },
    )

    _, keep = _parse(source, "note.json")
    _, journal = _parse(source, "journal.json")

    assert keep.records[0].record_type == journal.records[0].record_type == "note"
    assert keep.records[0].created_at == datetime.fromtimestamp(1789560000, tz=UTC)
    assert keep.records[0].metadata["google"]["isPinned"] is True
    assert journal.records[0].created_at == datetime.fromtimestamp(1789560000.5, tz=UTC)


def test_notebook_documents_and_assets_share_notebook_identity(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/NotebookLM/Notebook One/notebook.json": json.dumps(
                {
                    "title": "Synthetic notebook",
                    "metadata": {
                        "createTime": "2026-09-16T12:00:00Z",
                        "lastViewed": "2026-09-16T13:00:00Z",
                        "isShared": False,
                    },
                }
            ),
            "Takeout/NotebookLM/Notebook One/source.html": (
                "<html><body><p>Synthetic source text</p></body></html>"
            ),
            "Takeout/NotebookLM/Notebook One/Sources/source metadata.json": json.dumps(
                {"title": "Synthetic source", "sourceType": "web"}
            ),
            "Takeout/NotebookLM/Notebook One/audio.wav": b"synthetic-audio-overview",
        },
    )

    _, notebook = _parse(source, "notebook.json")
    _, document = _parse(source, "source.html")
    _, source_metadata = _parse(source, "source metadata.json")
    _, asset = _parse(source, "audio.wav")

    notebook_id = notebook.records[0].source_record_id
    assert notebook.records[0].record_type == "notebook"
    assert document.records[0].record_type == "document"
    assert "Synthetic source text" in (document.records[0].text or "")
    assert document.relationships[0].subject_source_id == notebook_id
    assert source_metadata.records[0].record_type == "document"
    assert source_metadata.relationships[0].subject_source_id == notebook_id
    assert asset.relationships[0].subject_source_id == notebook_id


def test_gemini_workspace_studio_and_absent_product_contracts(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Gemini/conversation.html": (
                "<div><b>Prompt</b><br>Synthetic response</div>"
            ),
            "Takeout/Gemini in Workspace/document.txt": "Synthetic workspace response",
            "Takeout/Workspace Studio/projects.json": "[]",
            "Takeout/Pinpoint/project.json": json.dumps(
                {"title": "Synthetic Pinpoint project", "future": True}
            ),
            "Takeout/Google Cloud Search/saved.csv": (
                "title,url\nSynthetic result,https://example.invalid/\n"
            ),
        },
    )

    _, gemini = _parse(source, "conversation.html")
    _, workspace = _parse(source, "document.txt")
    _, studio = _parse(source, "projects.json")
    _, pinpoint = _parse(source, "project.json")
    _, cloud_search = _parse(source, "saved.csv")

    assert gemini.records[0].record_type == workspace.records[0].record_type == "conversation"
    assert "Synthetic response" in (gemini.records[0].text or "")
    assert studio.record_count == 0
    assert pinpoint.record_count == cloud_search.record_count == 1
