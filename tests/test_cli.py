from __future__ import annotations

import json
from datetime import UTC, datetime

from personal_data_lab import cli
from personal_data_lab.models import ImportResult, NormalizedEvent, ParsedExport


def test_google_cli_reports_provenance_and_returns_two_for_conflicts(
    monkeypatch,
    capsys,
) -> None:
    event = NormalizedEvent(
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        source="google",
        event_type="search_activity",
        source_record_namespace="activity",
        source_record_id="activity:example",
    )
    parsed = ParsedExport(
        source="google",
        parser_version="google-my-activity-v1",
        category="my_activity/search/json",
        filename="Takeout/My Activity/Search/MyActivity.json",
        original_path="C:/takeout.zip!/Takeout/My Activity/Search/MyActivity.json",
        sha256="a" * 64,
        size_bytes=123,
        exported_at=datetime(2026, 9, 2, tzinfo=UTC),
        export_id="20260902T000000Z",
        snapshot_kind="partial",
        snapshot_scope="account/activity",
        events=(event,),
    )
    result = ImportResult(
        source_file_id=7,
        run_id=9,
        source_conversations=0,
        database_conversations=0,
        source_events=1,
        database_events=1,
        conflicted_events=1,
        new_event_versions=1,
    )
    monkeypatch.setattr(cli, "database_url", lambda override: "ignored")
    monkeypatch.setattr(cli, "create_database_engine", lambda url: object())
    monkeypatch.setattr(cli, "iter_google_exports", lambda source: iter((parsed,)))
    monkeypatch.setattr(cli, "import_parsed_exports", lambda engine, batch: (result,))

    exit_code = cli.main(["import-google", "takeout"])
    summary = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert summary["source_files"] == 1
    assert summary["conflicted_events"] == 1
    assert summary["reconciled"]
    assert summary["files"][0] == {
        "category": "my_activity/search/json",
        "filename": "Takeout/My Activity/Search/MyActivity.json",
        "original_path": "C:/takeout.zip!/Takeout/My Activity/Search/MyActivity.json",
        "sha256": "a" * 64,
        "size_bytes": 123,
        "exported_at": "2026-09-02T00:00:00Z",
        "export_id": "20260902T000000Z",
        "parser_version": "google-my-activity-v1",
        "source_file_id": 7,
        "run_id": 9,
        "source_conversations": 0,
        "database_conversations": 0,
        "source_events": 1,
        "database_events": 1,
        "inserted_conversations": 0,
        "updated_conversations": 0,
        "unchanged_conversations": 0,
        "stale_conversations": 0,
        "conflicted_conversations": 0,
        "tombstoned_conversations": 0,
        "new_conversation_versions": 0,
        "reused_conversation_versions": 0,
        "inserted_events": 0,
        "updated_events": 0,
        "unchanged_events": 0,
        "stale_events": 0,
        "conflicted_events": 1,
        "tombstoned_events": 0,
        "new_event_versions": 1,
        "reused_event_versions": 0,
        "short_circuited": False,
        "new_distinct_versions": 1,
        "reused_versions": 0,
        "reconciled": True,
    }
