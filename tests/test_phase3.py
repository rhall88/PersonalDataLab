"""Exit tests 1-5 from PHASE3_IMPLEMENTATION_PLAN.md (exit test 6 is the real-database rollout
spot-check, done live during PR D, not here). Mirrors tests/test_phase1.py's fixtures, extended
to also reset derive.metric_def/metric_dependency and measure.metric_daily between tests.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text

from personal_data_lab.compare import DateRange, compare_periods
from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.derive import run as derive_run
from personal_data_lab.derive import sync as derive_sync
from personal_data_lab.models import NormalizedConversation, NormalizedEvent, ParsedExport

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
                "measure.coverage, measure.metric_daily"
            )
        )
        connection.execute(
            text("TRUNCATE TABLE derive.metric_dependency, derive.metric_def RESTART IDENTITY CASCADE")
        )
        connection.execute(text("TRUNCATE TABLE declare.source_contract"))
    yield


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _declare_contract(
    engine: Engine,
    source: str,
    *,
    covered_start: str,
    covered_end: str,
    gap_semantics: str = "unknown",
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO declare.source_contract
                    (source, observable_kinds, covered_range_start, covered_range_end,
                     gap_semantics, default_timezone)
                VALUES (:source, ARRAY['event'], :start, :end, :gap_semantics, 'UTC')
                ON CONFLICT (source) DO UPDATE SET
                    observable_kinds = EXCLUDED.observable_kinds,
                    covered_range_start = EXCLUDED.covered_range_start,
                    covered_range_end = EXCLUDED.covered_range_end,
                    gap_semantics = EXCLUDED.gap_semantics,
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {"source": source, "start": covered_start, "end": covered_end, "gap_semantics": gap_semantics},
        )


def _import_events(engine: Engine, source: str, filename: str, specs: list[dict]) -> int:
    """specs: each {occurred_at, event_type, source_record_id, conversation_id=None,
    actor_identity=None}. Events sharing a conversation_id are nested under one
    NormalizedConversation; others are imported as standalone events.
    """
    by_conversation: dict[str, list[NormalizedEvent]] = {}
    standalone: list[NormalizedEvent] = []
    for spec in specs:
        normalized = NormalizedEvent(
            occurred_at=spec["occurred_at"],
            source=source,
            event_type=spec["event_type"],
            source_record_namespace=f"{source}/event",
            source_record_id=spec["source_record_id"],
            actor_identity=spec.get("actor_identity"),
        )
        conversation_id = spec.get("conversation_id")
        if conversation_id:
            by_conversation.setdefault(conversation_id, []).append(normalized)
        else:
            standalone.append(normalized)

    conversations = tuple(
        NormalizedConversation(
            source=source,
            source_conversation_id=conversation_id,
            source_record_namespace=f"{source}/conversation",
            events=tuple(events),
        )
        for conversation_id, events in by_conversation.items()
    )
    parsed = ParsedExport(
        source=source,
        parser_version="test-phase3-v1",
        filename=filename,
        original_path=filename,
        sha256=_sha(filename),
        size_bytes=1,
        events=tuple(standalone),
        conversations=conversations,
    )
    return import_parsed_exports(engine, (parsed,))[0].run_id


def _metric_value(engine: Engine, name: str, day: str) -> float | None:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT md.value FROM measure.metric_daily md "
                "JOIN derive.metric_def mdf ON mdf.id = md.metric_id "
                "WHERE mdf.name = :name AND md.bucket_start = :day"
            ),
            {"name": name, "day": day},
        ).scalar_one_or_none()


# --- exit test 1: derive sync upserts exactly 23 metric_def rows with their dependencies ------


def test_sync_upserts_twenty_three_metric_defs_with_dependencies(database_engine: Engine) -> None:
    derive_sync(database_engine)
    with database_engine.connect() as connection:
        names = set(connection.execute(text("SELECT name FROM derive.metric_def")).scalars())
        dependency_count = connection.execute(
            text("SELECT COUNT(DISTINCT metric_id) FROM derive.metric_dependency")
        ).scalar_one()

    assert len(names) == 23
    assert dependency_count == 23
    assert "communication.unique_correspondents_count" in names
    assert "messaging.total_messages_count" in names


# --- exit test 2: each per-source metric matches a hand-counted expectation -------------------


def test_per_source_metrics_match_hand_counted_expectations(database_engine: Engine) -> None:
    day = datetime(2024, 5, 1, 12, 0, tzinfo=UTC)
    for source in ("facebook", "imessages", "google", "anthropic", "openai"):
        _declare_contract(database_engine, source, covered_start="2024-05-01", covered_end="2024-05-01")

    _import_events(
        database_engine, "facebook", "fb.json",
        [
            {"occurred_at": day, "event_type": "message", "source_record_id": "fb-m1", "conversation_id": "fbconv1", "actor_identity": "Alice"},
            {"occurred_at": day, "event_type": "message", "source_record_id": "fb-m2", "conversation_id": "fbconv1", "actor_identity": "Bob"},
            {"occurred_at": day, "event_type": "message", "source_record_id": "fb-m3", "conversation_id": "fbconv2", "actor_identity": "Alice"},
            {"occurred_at": day, "event_type": "call", "source_record_id": "fb-c1", "conversation_id": "fbconv1", "actor_identity": "Bob"},
        ],
    )
    _import_events(
        database_engine, "imessages", "im.json",
        [
            {"occurred_at": day, "event_type": "message.incoming", "source_record_id": "im-1", "conversation_id": "imsg1", "actor_identity": "+15551234567"},
            {"occurred_at": day, "event_type": "message.incoming", "source_record_id": "im-2", "conversation_id": "imsg1", "actor_identity": "+15551234567"},
            {"occurred_at": day, "event_type": "message.outgoing", "source_record_id": "im-3", "conversation_id": "imsg1", "actor_identity": "you"},
            {"occurred_at": day, "event_type": "message.outgoing", "source_record_id": "im-4", "conversation_id": "imsg1", "actor_identity": "you"},
            {"occurred_at": day, "event_type": "message.outgoing", "source_record_id": "im-5", "conversation_id": "imsg2", "actor_identity": "you"},
            {"occurred_at": day, "event_type": "message.reaction", "source_record_id": "im-6", "conversation_id": "imsg1"},
            {"occurred_at": day, "event_type": "message.edit", "source_record_id": "im-7", "conversation_id": "imsg2"},
        ],
    )
    _import_events(
        database_engine, "google", "g.json",
        [
            {"occurred_at": day, "event_type": "mail_message", "source_record_id": "g-1", "conversation_id": "gmail1", "actor_identity": "alice@example.com"},
            {"occurred_at": day, "event_type": "mail_message", "source_record_id": "g-2", "conversation_id": "gmail1", "actor_identity": "alice@example.com"},
            {"occurred_at": day, "event_type": "mail_message", "source_record_id": "g-3", "conversation_id": "gmail1", "actor_identity": "alice@example.com"},
            {"occurred_at": day, "event_type": "mail_message", "source_record_id": "g-4", "conversation_id": "gmail2", "actor_identity": "carol@example.com"},
            {"occurred_at": day, "event_type": "chat_message", "source_record_id": "g-5", "conversation_id": "gchat1", "actor_identity": "dave@example.com"},
            {"occurred_at": day, "event_type": "chat_message", "source_record_id": "g-6", "conversation_id": "gchat1", "actor_identity": "dave@example.com"},
            {"occurred_at": day, "event_type": "voice_communication", "source_record_id": "g-7", "actor_identity": "eve@example.com"},
            {"occurred_at": day, "event_type": "meet_call", "source_record_id": "g-8", "actor_identity": "account_owner"},
        ],
    )
    _import_events(
        database_engine, "anthropic", "a.json",
        [
            {"occurred_at": day, "event_type": "conversation_message", "source_record_id": f"a-{i}", "conversation_id": "aconv1"}
            for i in range(3)
        ]
        + [
            {"occurred_at": day, "event_type": "conversation_message", "source_record_id": f"a-{i}", "conversation_id": "aconv2"}
            for i in range(3, 5)
        ],
    )
    _import_events(
        database_engine, "openai", "o.json",
        [
            {"occurred_at": day, "event_type": "conversation_message", "source_record_id": f"o-{i}", "conversation_id": "oconv1"}
            for i in range(6)
        ],
    )

    derive_sync(database_engine)
    derive_run(database_engine)
    derive_sync(database_engine)
    derive_run(database_engine, include_global=True)

    expected = {
        "facebook.messages_count": 3,
        "facebook.calls_count": 1,
        "facebook.active_conversations_count": 2,
        "imessages.incoming_count": 2,
        "imessages.outgoing_count": 3,
        "imessages.reactions_count": 1,
        "imessages.edits_count": 1,
        "imessages.active_conversations_count": 2,
        "google.mail_messages_count": 4,
        "google.chat_messages_count": 2,
        "google.voice_communications_count": 1,
        "google.meet_calls_count": 1,
        "google.active_conversations_count": 3,
        "anthropic.chat_turns_count": 5,
        "anthropic.active_conversations_count": 2,
        "openai.chat_turns_count": 6,
        "openai.active_conversations_count": 1,
        "messaging.total_messages_count": 3 + 2 + 3 + 4 + 2,
        "ai_chat.total_turns_count": 5 + 6,
        # facebook: Alice, Bob (2); imessages: +15551234567 only ("you" excluded as role_label,
        # both incoming numbers collapse to one distinct pair) (1); google: alice/carol/dave/eve
        # (account_owner excluded) (4).
        "communication.unique_correspondents_count": 2 + 1 + 4,
    }
    for name, expected_value in expected.items():
        actual = _metric_value(database_engine, name, "2024-05-01")
        assert actual == expected_value, f"{name}: expected {expected_value}, got {actual}"


# --- exit test 3: a day outside the declared covered_range gets no row, never value = 0 -------


def test_day_outside_covered_range_produces_no_row(database_engine: Engine) -> None:
    _declare_contract(database_engine, "facebook", covered_start="2024-06-01", covered_end="2024-06-01")
    _import_events(
        database_engine, "facebook", "fb.json",
        [
            {"occurred_at": datetime(2024, 6, 1, tzinfo=UTC), "event_type": "message", "source_record_id": "in-range"},
            {"occurred_at": datetime(2024, 6, 2, tzinfo=UTC), "event_type": "message", "source_record_id": "out-of-range"},
        ],
    )
    derive_sync(database_engine)
    derive_run(database_engine)

    assert _metric_value(database_engine, "facebook.messages_count", "2024-06-01") == 1
    assert _metric_value(database_engine, "facebook.messages_count", "2024-06-02") is None


# --- exit test 4: a composite's day is known only when every constituent is known -------------


def test_composite_matches_sum_only_when_every_constituent_is_known(database_engine: Engine) -> None:
    _declare_contract(database_engine, "facebook", covered_start="2024-07-01", covered_end="2024-07-01")
    _declare_contract(database_engine, "imessages", covered_start="2024-07-01", covered_end="2024-07-01")
    _declare_contract(database_engine, "google", covered_start="2024-07-01", covered_end="2024-07-02")

    _import_events(
        database_engine, "facebook", "fb.json",
        [{"occurred_at": datetime(2024, 7, 1, tzinfo=UTC), "event_type": "message", "source_record_id": "fb-1"}],
    )
    _import_events(
        database_engine, "imessages", "im.json",
        [
            {"occurred_at": datetime(2024, 7, 1, tzinfo=UTC), "event_type": "message.incoming", "source_record_id": "im-1"},
            {"occurred_at": datetime(2024, 7, 1, tzinfo=UTC), "event_type": "message.outgoing", "source_record_id": "im-2"},
        ],
    )
    _import_events(
        database_engine, "google", "g.json",
        [
            {"occurred_at": datetime(2024, 7, 1, tzinfo=UTC), "event_type": "mail_message", "source_record_id": "g-1"},
            {"occurred_at": datetime(2024, 7, 1, tzinfo=UTC), "event_type": "chat_message", "source_record_id": "g-2"},
            {"occurred_at": datetime(2024, 7, 2, tzinfo=UTC), "event_type": "mail_message", "source_record_id": "g-3"},
            {"occurred_at": datetime(2024, 7, 2, tzinfo=UTC), "event_type": "mail_message", "source_record_id": "g-4"},
            {"occurred_at": datetime(2024, 7, 2, tzinfo=UTC), "event_type": "mail_message", "source_record_id": "g-5"},
        ],
    )

    derive_sync(database_engine)
    derive_run(database_engine)
    derive_sync(database_engine)
    derive_run(database_engine, include_global=True)

    # 2024-07-01: facebook, imessages, and google are all covered -- a real sum.
    assert _metric_value(database_engine, "messaging.total_messages_count", "2024-07-01") == 1 + 1 + 1 + 1 + 1
    # 2024-07-02: facebook/imessages have no coverage attestation at all -- even though google's
    # own two constituent metrics are known that day (mail=3, chat=0), the composite must not
    # silently sum just the sources it has and understate the true total.
    assert _metric_value(database_engine, "google.mail_messages_count", "2024-07-02") == 3
    assert _metric_value(database_engine, "google.chat_messages_count", "2024-07-02") == 0
    assert _metric_value(database_engine, "messaging.total_messages_count", "2024-07-02") is None


# --- exit test 5: compare ranks by change and reports known/total days accurately -------------


def test_compare_ranks_by_change_and_reports_coverage_gaps(database_engine: Engine) -> None:
    # Only 2024-05-01 is ever declared covered for the baseline window: source_contract holds
    # exactly one covered_range at a time, so the contract is narrowed to just that one day,
    # derived, then moved to the focal window -- 05-02/05-03 are never covered under either
    # declaration, leaving a real, deliberate coverage gap in the baseline period.
    _declare_contract(database_engine, "facebook", covered_start="2024-05-01", covered_end="2024-05-01")
    _import_events(
        database_engine, "facebook", "fb-2024-05-01.json",
        [
            {"occurred_at": datetime(2024, 5, 1, tzinfo=UTC), "event_type": "message", "source_record_id": f"fb-05-01-{i}"}
            for i in range(10)
        ],
    )
    derive_sync(database_engine)
    derive_run(database_engine)

    _declare_contract(database_engine, "facebook", covered_start="2024-06-01", covered_end="2024-06-03")
    for day, count in (("2024-06-01", 5), ("2024-06-02", 7), ("2024-06-03", 9)):
        _import_events(
            database_engine, "facebook", f"fb-{day}.json",
            [
                {
                    "occurred_at": datetime.fromisoformat(day).replace(tzinfo=UTC),
                    "event_type": "message",
                    "source_record_id": f"fb-{day}-{i}",
                }
                for i in range(count)
            ],
        )
    derive_sync(database_engine)
    derive_run(database_engine)

    ranked = compare_periods(
        database_engine,
        focal=DateRange(start=datetime(2024, 6, 1, tzinfo=UTC).date(), end=datetime(2024, 6, 4, tzinfo=UTC).date()),
        baseline=DateRange(start=datetime(2024, 5, 1, tzinfo=UTC).date(), end=datetime(2024, 5, 4, tzinfo=UTC).date()),
        metric_names=("facebook.messages_count",),
    )

    assert len(ranked) == 1
    row = ranked[0]
    assert row["metric"] == "facebook.messages_count"
    assert row["focal_value"] == 5 + 7 + 9
    assert row["focal_known_days"] == 3
    assert row["focal_total_days"] == 3
    # Only 2024-05-01 is covered in the baseline window; 05-02/05-03 have no source_contract
    # coverage at all, so no metric_daily row -- known_days must show that gap, not silently
    # treat the missing days as zero activity.
    assert row["baseline_value"] == 10
    assert row["baseline_known_days"] == 1
    assert row["baseline_total_days"] == 3
    assert row["absolute_change"] == 11
    assert row["percent_change"] == pytest.approx(110.0)
