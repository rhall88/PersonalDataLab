"""Exit tests 1-5 from PHASE1_IMPLEMENTATION_PLAN.md (exit test 6 is the real-database rollout
spot-check, done live during PR C, not here). Mirrors tests/test_derive.py's fixtures exactly,
extended to also reset declare.source_contract and measure.coverage between tests.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text

from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.derive import REGISTRY
from personal_data_lab.derive import run as derive_run
from personal_data_lab.derive import sync as derive_sync
from personal_data_lab.derive.asof import as_of_events, as_of_records, resolve_cutoff_run_id
from personal_data_lab.models import NormalizedEvent, NormalizedRecord, ParsedExport

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    test_database_url = os.getenv("TEST_DATABASE_URL")
    if not test_database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_database_engine(test_database_url)
    with engine.connect() as connection:
        database_name = str(connection.execute(text("SELECT current_database()")).scalar_one())
    if not database_name.endswith("_test"):
        engine.dispose()
        pytest.fail("integration tests require a database name ending in '_test'")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(database_engine: Engine) -> Iterator[None]:
    with database_engine.begin() as connection:
        connection.execute(
            text(
                """
                TRUNCATE TABLE
                    relationship_observations, asset_observations, record_observations,
                    event_observations, conversation_observations, attachment_observations,
                    event_versions, conversation_versions, attachment_versions,
                    asset_versions, record_versions, relationship_versions,
                    import_run_source_files, source_file_locations, attachments,
                    source_file_assets, source_file_relationships, source_file_records,
                    source_file_events, source_file_conversations, events, assets, records,
                    relationships, identities, people, conversations, source_files, import_runs
                RESTART IDENTITY CASCADE
                """
            )
        )
        # Per-run derive state, reset so each test starts from "nothing has ever been computed".
        connection.execute(
            text(
                "TRUNCATE TABLE derive.derivation_run, derive.partition_state, "
                "derive.evidence_epoch RESTART IDENTITY CASCADE"
            )
        )
        connection.execute(text("UPDATE derive.change_cursor SET last_import_run_id = 0"))
        connection.execute(
            text(
                "TRUNCATE TABLE measure.source_activity_daily, measure.source_activity_7d, "
                "measure.coverage"
            )
        )
        connection.execute(text("TRUNCATE TABLE declare.source_contract"))
    yield


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _declare_contract(
    engine: Engine,
    source: str,
    *,
    observable_kinds: list[str],
    covered_start: str | None,
    covered_end: str | None,
    gap_semantics: str,
    default_timezone: str = "UTC",
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO declare.source_contract
                    (source, observable_kinds, covered_range_start, covered_range_end,
                     gap_semantics, default_timezone)
                VALUES
                    (:source, :observable_kinds, :covered_start, :covered_end,
                     :gap_semantics, :default_timezone)
                ON CONFLICT (source) DO UPDATE SET
                    observable_kinds = EXCLUDED.observable_kinds,
                    covered_range_start = EXCLUDED.covered_range_start,
                    covered_range_end = EXCLUDED.covered_range_end,
                    gap_semantics = EXCLUDED.gap_semantics,
                    default_timezone = EXCLUDED.default_timezone,
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {
                "source": source,
                "observable_kinds": observable_kinds,
                "covered_start": covered_start,
                "covered_end": covered_end,
                "gap_semantics": gap_semantics,
                "default_timezone": default_timezone,
            },
        )


def _import_event(
    engine: Engine, source: str, filename: str, occurred_at: datetime, event_id: str
) -> int:
    parsed = ParsedExport(
        source=source,
        parser_version="test-phase1-v1",
        filename=filename,
        original_path=filename,
        sha256=_sha(filename),
        size_bytes=1,
        events=(
            NormalizedEvent(
                occurred_at=occurred_at,
                source=source,
                event_type="message",
                source_record_namespace=f"{source}/message/event",
                source_record_id=event_id,
                text=f"event {event_id}",
            ),
        ),
    )
    return import_parsed_exports(engine, (parsed,))[0].run_id


def _import_record(
    engine: Engine,
    source: str,
    filename: str,
    *,
    record_id: str,
    title: str,
    exported_at: datetime,
    lifecycle_state: str = "active",
) -> int:
    parsed = ParsedExport(
        source=source,
        parser_version="test-phase1-v1",
        filename=filename,
        original_path=filename,
        sha256=_sha(filename),
        size_bytes=1,
        exported_at=exported_at,
        records=(
            NormalizedRecord(
                source=source,
                record_type="note",
                source_record_namespace=f"{source}/note/record",
                source_record_id=record_id,
                title=title,
                occurred_at=exported_at,
                lifecycle_state=lifecycle_state,
            ),
        ),
    )
    return import_parsed_exports(engine, (parsed,))[0].run_id


# --- exit test 1: as-of reconstruction matches a stored snapshot (synthetic) ------------------


def test_as_of_records_reflects_insert_update_and_tombstone_across_three_runs(
    database_engine: Engine,
) -> None:
    run1 = _import_record(
        database_engine, "testsource", "r1.json",
        record_id="rec-1", title="v1", exported_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    run2 = _import_record(
        database_engine, "testsource", "r2.json",
        record_id="rec-1", title="v2", exported_at=datetime(2024, 1, 2, tzinfo=UTC),
    )
    run3 = _import_record(
        database_engine, "testsource", "r3.json",
        record_id="rec-1", title="v3", exported_at=datetime(2024, 1, 3, tzinfo=UTC),
        lifecycle_state="provider_deleted",
    )

    with database_engine.connect() as connection:
        after_run1 = as_of_records(connection, run1)
        after_run2 = as_of_records(connection, run2)
        after_run3 = as_of_records(connection, run3)

    assert [r["version_normalized_payload"]["title"] for r in after_run1] == ["v1"]
    assert [r["version_normalized_payload"]["title"] for r in after_run2] == ["v2"]
    assert after_run3 == []  # tombstoned by run3: absent as of the tombstoning run, not before


def test_as_of_events_is_visible_immediately_after_its_own_import(database_engine: Engine) -> None:
    run_id = _import_event(
        database_engine, "eventsrc", "a.json", datetime(2024, 5, 1, tzinfo=UTC), "e1"
    )
    with database_engine.connect() as connection:
        visible = as_of_events(connection, run_id)
        not_yet_visible = as_of_events(connection, 0)
    assert [r["source_record_id"] for r in visible] == ["e1"]
    assert not_yet_visible == []


def test_resolve_cutoff_run_id_requires_exactly_one_argument(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        with pytest.raises(ValueError):
            resolve_cutoff_run_id(connection, cutoff_run_id=None, cutoff_at=None)
        with pytest.raises(ValueError):
            resolve_cutoff_run_id(
                connection, cutoff_run_id=1, cutoff_at=datetime(2024, 1, 1, tzinfo=UTC)
            )


def test_resolve_cutoff_run_id_from_a_timestamp(database_engine: Engine) -> None:
    run_id = _import_event(
        database_engine, "cutoffsrc", "a.json", datetime(2024, 3, 15, tzinfo=UTC), "e1"
    )
    with database_engine.connect() as connection:
        resolved = resolve_cutoff_run_id(
            connection, cutoff_run_id=None, cutoff_at=datetime(2099, 1, 1, tzinfo=UTC)
        )
    assert resolved == run_id


# --- exit test 2: declared not_exported gap semantics doesn't get flagged as a gap ------------


def test_coverage_emits_no_row_for_a_period_outside_the_declared_covered_range(
    database_engine: Engine,
) -> None:
    _declare_contract(
        database_engine, "notexported_source",
        observable_kinds=["event"],
        covered_start="2024-03-01", covered_end="2024-03-31",
        gap_semantics="not_exported",
    )
    _import_event(
        database_engine, "notexported_source", "a.json",
        datetime(2024, 4, 15, tzinfo=UTC), "e1",
    )
    derive_sync(database_engine)
    derive_run(database_engine)

    with database_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT * FROM measure.coverage WHERE source = :source AND bucket_start >= '2024-04-01'"
            ),
            {"source": "notexported_source"},
        ).all()
    assert rows == []


def test_coverage_emits_no_row_for_a_source_with_no_declared_contract(
    database_engine: Engine,
) -> None:
    _import_event(
        database_engine, "undeclared_source", "a.json", datetime(2024, 3, 15, tzinfo=UTC), "e1"
    )
    derive_sync(database_engine)
    derive_run(database_engine)

    with database_engine.connect() as connection:
        rows = connection.execute(
            text("SELECT * FROM measure.coverage WHERE source = 'undeclared_source'")
        ).all()
    assert rows == []


# --- exit test 3: coverage only counts watched kinds; full days are not gap-flagged -----------


def test_coverage_counts_only_watched_kinds_and_full_days_are_not_gap_flagged(
    database_engine: Engine,
) -> None:
    _declare_contract(
        database_engine, "coveragesrc",
        observable_kinds=["event"],
        covered_start="2024-03-01", covered_end="2024-03-31",
        gap_semantics="unknown",
    )
    _import_event(database_engine, "coveragesrc", "a.json", datetime(2024, 3, 15, tzinfo=UTC), "e1")
    _import_record(
        database_engine, "coveragesrc", "r.json",
        record_id="rec-1", title="not observable", exported_at=datetime(2024, 3, 15, tzinfo=UTC),
    )
    derive_sync(database_engine)
    derive_run(database_engine)

    with database_engine.connect() as connection:
        covered_day = connection.execute(
            text(
                "SELECT expected, observed, completeness, gap_reason FROM measure.coverage "
                "WHERE source = 'coveragesrc' AND bucket_start = '2024-03-15' "
                "AND object_kind = 'event'"
            )
        ).one()
        gap_day = connection.execute(
            text(
                "SELECT expected, observed, completeness, gap_reason FROM measure.coverage "
                "WHERE source = 'coveragesrc' AND bucket_start = '2024-03-16' "
                "AND object_kind = 'event'"
            )
        ).one()
        record_rows = connection.execute(
            text(
                "SELECT 1 FROM measure.coverage WHERE source = 'coveragesrc' "
                "AND object_kind = 'record'"
            )
        ).all()

    assert (covered_day.expected, covered_day.observed) == (1, 1)
    assert covered_day.completeness == 1.0
    assert covered_day.gap_reason is None
    assert (gap_day.expected, gap_day.observed) == (1, 0)
    assert gap_day.completeness == 0.0
    assert gap_day.gap_reason == "unknown"
    assert record_rows == []  # "record" is not in observable_kinds for this source


# --- exit test 4: backfilling a historical gap re-triggers recomputation, no new invalidation --


def test_backfilling_a_historical_gap_retriggers_coverage_via_the_existing_change_feed(
    database_engine: Engine,
) -> None:
    _declare_contract(
        database_engine, "backfillsrc",
        observable_kinds=["event"],
        covered_start="2024-03-01", covered_end="2024-03-31",
        gap_semantics="unknown",
    )
    _import_event(database_engine, "backfillsrc", "a.json", datetime(2024, 3, 15, tzinfo=UTC), "e1")
    derive_sync(database_engine)
    derive_run(database_engine)

    with database_engine.connect() as connection:
        before = connection.execute(
            text(
                "SELECT observed FROM measure.coverage WHERE source = 'backfillsrc' "
                "AND bucket_start = '2024-03-10' AND object_kind = 'event'"
            )
        ).scalar_one()
    assert before == 0

    _import_event(database_engine, "backfillsrc", "b.json", datetime(2024, 3, 10, tzinfo=UTC), "e2")
    sync_outcome = derive_sync(database_engine)
    assert not sync_outcome.is_noop
    derive_run(database_engine)

    with database_engine.connect() as connection:
        after = connection.execute(
            text(
                "SELECT observed, completeness, gap_reason FROM measure.coverage "
                "WHERE source = 'backfillsrc' AND bucket_start = '2024-03-10' "
                "AND object_kind = 'event'"
            )
        ).one()
    assert after.observed == 1
    assert after.completeness == 1.0
    assert after.gap_reason is None


# --- exit test 5: no canonical table is altered by migration 011 (structural) -----------------


def test_migration_011_adds_no_canonical_columns(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        columns = connection.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name IN ('conversations', 'events', 'records', 'assets', "
                "'relationships')"
            )
        ).all()
    assert not any(
        "contract" in row.column_name or "coverage" in row.column_name for row in columns
    )


# --- registry sanity: REGISTRY is what the CLI/worker actually use -----------------------------


def test_measure_coverage_is_registered() -> None:
    assert "measure_coverage" in {d.name for d in REGISTRY}
