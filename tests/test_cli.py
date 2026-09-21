from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

import pytest

from personal_data_lab import cli
from personal_data_lab.config import DatabaseTargetGuardError
from personal_data_lab.google_importer import GoogleExportError
from personal_data_lab.migrations import LATEST_SCHEMA_VERSION
from personal_data_lab.models import ImportResult, NormalizedEvent, ParsedExport


def test_google_cli_reports_provenance_and_returns_two_for_conflicts(
    monkeypatch,
    capsys,
) -> None:
    event = NormalizedEvent(
        occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
        source="google",
        event_type="search_activity",
        source_record_namespace="activity/search/json",
        source_record_id="activity:example",
        text="PRIVATE ACTIVITY TEXT",
        metadata={"google": {"private": "PROVIDER PAYLOAD"}},
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
    monkeypatch.setattr(
        cli, "database_url", lambda override: "postgresql://user:pass@localhost/ignored"
    )
    monkeypatch.setattr(cli, "create_database_engine", lambda url: object())

    def fake_google_exports(source, *, on_member_start):
        on_member_start(parsed.original_path, parsed.category, parsed.size_bytes)
        yield parsed

    monkeypatch.setattr(cli, "iter_google_exports", fake_google_exports)
    monkeypatch.setattr(cli, "import_parsed_exports", lambda engine, batch: (result,))

    exit_code = cli.main(["import-google", "takeout"])
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

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
        "source_relationships": 0,
        "database_relationships": 0,
        "inserted_relationships": 0,
        "updated_relationships": 0,
        "unchanged_relationships": 0,
        "stale_relationships": 0,
        "conflicted_relationships": 0,
        "tombstoned_relationships": 0,
        "new_relationship_versions": 0,
        "reused_relationship_versions": 0,
        "short_circuited": False,
        "new_distinct_versions": 1,
        "reused_versions": 0,
        "reconciled": True,
    }
    assert "[google] parsing" in captured.err
    assert "[google] parsed" in captured.err
    assert "[google] imported" in captured.err
    assert "category=my_activity/search/json" in captured.err
    assert "size_bytes=123" in captured.err
    assert "source_events=1" in captured.err
    assert "inserted=0 unchanged=0 conflicted=1" in captured.err
    assert "PRIVATE ACTIVITY TEXT" not in captured.err
    assert "PROVIDER PAYLOAD" not in captured.err


def test_google_cli_parse_failure_identifies_member_without_payload(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    source = tmp_path / "takeout-20260909T012526Z-001.zip"
    member = "Takeout/My Activity/Search/MyActivity.json"
    with ZipFile(source, "w") as archive:
        archive.writestr(member, '[{"title":"PRIVATE FAILURE TEXT"}')
    monkeypatch.setattr(
        cli, "database_url", lambda override: "postgresql://user:pass@localhost/ignored"
    )
    monkeypatch.setattr(cli, "create_database_engine", lambda url: object())

    with pytest.raises(GoogleExportError, match="ended unexpectedly"):
        cli.main(["import-google", str(source)])

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[google] parsing" in captured.err
    assert member in captured.err
    assert "category=my_activity/search/json" in captured.err
    assert "PRIVATE FAILURE TEXT" not in captured.err


def test_import_google_all_refuses_test_like_database_without_pdl_env(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "database_url",
        lambda override: "postgresql://user:pass@localhost/personaldata_google_acceptance_test",
    )
    monkeypatch.setattr(
        cli,
        "create_database_engine",
        lambda url: pytest.fail("guard did not short-circuit before engine creation"),
    )
    monkeypatch.delenv("PDL_ENV", raising=False)

    with pytest.raises(DatabaseTargetGuardError, match="test/acceptance naming pattern"):
        cli.main(["import-google-all", "takeout"])


def test_doctor_reports_canonical_target_with_clean_json(monkeypatch, capsys) -> None:
    monkeypatch.delenv("PDL_ENV", raising=False)
    monkeypatch.delenv("PDL_CANONICAL_DB_NAME", raising=False)
    monkeypatch.setattr(
        cli, "database_url", lambda override: "postgresql://user:pass@localhost:5432/personaldata"
    )
    monkeypatch.setattr(cli, "create_database_engine", lambda url: object())
    monkeypatch.setattr(cli, "current_schema_version", lambda engine: LATEST_SCHEMA_VERSION)
    monkeypatch.setattr(
        cli,
        "list_sibling_databases",
        lambda engine: [
            ("personaldata", 1000),
            ("personaldata_google_acceptance_test", 2000),
            ("postgres", 300),
        ],
    )

    assert cli.main(["doctor"]) == 0

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["target_database"] == "personaldata"
    assert report["target_classification"] == "canonical"
    assert report["schema_version"] == LATEST_SCHEMA_VERSION
    assert report["schema_up_to_date"] is True
    assert report["suspicious_config"] == []
    classifications = {row["name"]: row["classification"] for row in report["sibling_databases"]}
    assert classifications == {
        "personaldata": "canonical",
        "personaldata_google_acceptance_test": "test-like",
        "postgres": "unclassified",
    }
    assert report["test_like_sibling_count"] == 1


def test_doctor_flags_unclassified_target_and_bad_pdl_env(monkeypatch, capsys) -> None:
    monkeypatch.setenv("PDL_ENV", "typo")
    monkeypatch.delenv("PDL_CANONICAL_DB_NAME", raising=False)
    monkeypatch.setattr(
        cli, "database_url", lambda override: "postgresql://user:pass@localhost:5432/some_other_db"
    )
    monkeypatch.setattr(cli, "create_database_engine", lambda url: object())
    monkeypatch.setattr(cli, "current_schema_version", lambda engine: 5)
    monkeypatch.setattr(cli, "list_sibling_databases", lambda engine: [])

    assert cli.main(["doctor"]) == 0

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["target_classification"] == "unclassified"
    assert report["schema_up_to_date"] is False
    assert len(report["suspicious_config"]) == 2
    assert any("PDL_ENV" in item for item in report["suspicious_config"])
    assert any("neither the configured canonical" in item for item in report["suspicious_config"])


def test_doctor_table_format_includes_sibling_databases(monkeypatch, capsys) -> None:
    monkeypatch.delenv("PDL_ENV", raising=False)
    monkeypatch.delenv("PDL_CANONICAL_DB_NAME", raising=False)
    monkeypatch.setattr(
        cli, "database_url", lambda override: "postgresql://user:pass@localhost:5432/personaldata"
    )
    monkeypatch.setattr(cli, "create_database_engine", lambda url: object())
    monkeypatch.setattr(cli, "current_schema_version", lambda engine: 7)
    monkeypatch.setattr(cli, "list_sibling_databases", lambda engine: [("personaldata", 1000)])

    assert cli.main(["doctor", "--format", "table"]) == 0

    captured = capsys.readouterr()
    assert "target classification: canonical" in captured.out
    assert "personaldata" in captured.out
    assert "THIS TARGET" in captured.out
    assert "suspicious config: none" in captured.out


def test_doctor_does_not_apply_import_guards_to_test_like_target(monkeypatch, capsys) -> None:
    # doctor must be able to report on any target, including one the import
    # guards would refuse — it never calls _resolve_guarded_engine.
    monkeypatch.delenv("PDL_ENV", raising=False)
    monkeypatch.setattr(
        cli,
        "database_url",
        lambda override: (
            "postgresql://user:pass@localhost:5432/personaldata_google_acceptance_test"
        ),
    )
    monkeypatch.setattr(cli, "create_database_engine", lambda url: object())
    monkeypatch.setattr(cli, "current_schema_version", lambda engine: 7)
    monkeypatch.setattr(cli, "list_sibling_databases", lambda engine: [])

    assert cli.main(["doctor"]) == 0

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["target_classification"] == "test-like"
