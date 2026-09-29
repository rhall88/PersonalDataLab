"""Exit tests 1-5 from PHASE4_IMPLEMENTATION_PLAN.md (exit test 6 is the real-database rollout,
done live during PR D, not here). Mirrors tests/test_phase3.py's fixtures, extended to also reset
Phase 4's new tables (signal.*, measure.interaction_dynamics, episode.episode) between tests.
"""

from __future__ import annotations

import hashlib
import os
import statistics
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text

from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.derive import run as derive_run
from personal_data_lab.derive import sync as derive_sync
from personal_data_lab.derive.derivers.episode import declare_episode, list_episodes
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
                "measure.coverage, measure.metric_daily, measure.interaction_dynamics"
            )
        )
        connection.execute(
            text("TRUNCATE TABLE derive.metric_dependency, derive.metric_def RESTART IDENTITY CASCADE")
        )
        connection.execute(text("TRUNCATE TABLE declare.source_contract"))
        connection.execute(
            text(
                "TRUNCATE TABLE signal.baseline, signal.change_point, signal.anomaly, "
                "signal.interaction_state RESTART IDENTITY CASCADE"
            )
        )
        connection.execute(text("TRUNCATE TABLE episode.episode RESTART IDENTITY CASCADE"))
    yield


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _declare_contract(engine: Engine, source: str, *, covered_start: str, covered_end: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO declare.source_contract
                    (source, observable_kinds, covered_range_start, covered_range_end,
                     gap_semantics, default_timezone)
                VALUES (:source, ARRAY['event'], :start, :end, 'unknown', 'UTC')
                ON CONFLICT (source) DO UPDATE SET
                    covered_range_start = EXCLUDED.covered_range_start,
                    covered_range_end = EXCLUDED.covered_range_end,
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {"source": source, "start": covered_start, "end": covered_end},
        )


def _import_events(engine: Engine, source: str, filename: str, specs: list[dict]) -> int:
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
        parser_version="test-phase4-v1",
        filename=filename,
        original_path=filename,
        sha256=_sha(filename),
        size_bytes=1,
        events=tuple(standalone),
        conversations=conversations,
    )
    return import_parsed_exports(engine, (parsed,))[0].run_id


def _link_facebook_owner(engine: Engine, identity_value: str = "Ry Owner") -> None:
    with engine.begin() as connection:
        person_id = connection.execute(
            text("INSERT INTO people (display_name) VALUES ('Ry') RETURNING id")
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO identity_links (person_id, source, identity_value, tier) "
                "VALUES (:pid, 'facebook', :value, 'declared')"
            ),
            {"pid": person_id, "value": identity_value},
        )


def _shift_day_counts(n_days: int, shift_at: int, baseline_pattern: list[int], shifted_value: int) -> list[int]:
    counts = []
    for i in range(n_days):
        if i < shift_at:
            counts.append(baseline_pattern[i % len(baseline_pattern)])
        else:
            counts.append(shifted_value)
    return counts


def _facebook_message_specs(day_counts: list[int], start_ordinal: int = 0) -> list[dict]:
    specs = []
    counter = 0
    base = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    for day_index, count in enumerate(day_counts):
        occurred = base + timedelta(days=start_ordinal + day_index)
        for _ in range(count):
            counter += 1
            specs.append(
                {
                    "occurred_at": occurred,
                    "event_type": "message",
                    "source_record_id": f"fb-shift-{start_ordinal}-{counter}",
                    "conversation_id": f"fb-shift-conv-{start_ordinal}-{counter}",
                    "actor_identity": "Correspondent",
                }
            )
    return specs


def _imessages_incoming_specs(day_counts: list[int], start_ordinal: int = 0) -> list[dict]:
    specs = []
    counter = 0
    base = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    for day_index, count in enumerate(day_counts):
        occurred = base + timedelta(days=start_ordinal + day_index)
        for _ in range(count):
            counter += 1
            specs.append(
                {
                    "occurred_at": occurred,
                    "event_type": "message.incoming",
                    "source_record_id": f"im-shift-{start_ordinal}-{counter}",
                    "conversation_id": f"im-shift-conv-{start_ordinal}-{counter}",
                    "actor_identity": "+15550001111",
                }
            )
    return specs


# --- exit test 1: sync registers Phase 4's derivers -------------------------------------------


def test_sync_registers_phase4_derivers(database_engine: Engine) -> None:
    derive_sync(database_engine)
    with database_engine.connect() as connection:
        names = set(connection.execute(text("SELECT name FROM derive.deriver")).scalars())
    for expected in (
        "signal_baseline", "signal_change_point", "signal_anomaly",
        "measure_interaction_dynamics", "signal_interaction_state", "episode_detect_candidates",
    ):
        assert expected in names


# --- exit tests 2-3: baselines/change points/anomalies on a synthetic level shift; gating ------


def test_signals_gated_and_detect_synthetic_shift_with_anomaly(database_engine: Engine) -> None:
    baseline_pattern = [2, 3, 2, 4, 2, 3]
    shift_at = 40
    shifted_value = 30
    day_counts = _shift_day_counts(50, shift_at, baseline_pattern, shifted_value)

    _declare_contract(database_engine, "facebook", covered_start="2024-01-01", covered_end="2024-02-20")
    _import_events(database_engine, "facebook", "fb-shift.json", _facebook_message_specs(day_counts))

    # imessages: only 5 days of history -- far below _MIN_TRAILING_OBS, so no baseline row should
    # ever appear for it (the gating exit test).
    _declare_contract(database_engine, "imessages", covered_start="2024-01-01", covered_end="2024-01-06")
    _import_events(
        database_engine, "imessages", "im-short.json",
        _imessages_incoming_specs([1, 1, 1, 1, 1]),
    )

    derive_sync(database_engine)
    derive_run(database_engine)
    derive_sync(database_engine)
    derive_run(database_engine, include_global=True)

    with database_engine.connect() as connection:
        # 5 known days is below _MIN_TRAILING_OBS (14), so no trailing_90d row should ever
        # appear -- but it meets _MIN_LIFETIME_OBS (5) exactly, so one lifetime snapshot is
        # expected. This is the gating exit test for trailing_90d specifically.
        imessages_trailing_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM signal.baseline b "
                "JOIN derive.metric_def m ON m.id = b.metric_id "
                "WHERE m.name = 'imessages.incoming_count' AND b.kind = 'trailing_90d'"
            )
        ).scalar_one()
        assert imessages_trailing_count == 0
        imessages_lifetime_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM signal.baseline b "
                "JOIN derive.metric_def m ON m.id = b.metric_id "
                "WHERE m.name = 'imessages.incoming_count' AND b.kind = 'lifetime'"
            )
        ).scalar_one()
        assert imessages_lifetime_count == 1

        fb_metric_id = connection.execute(
            text("SELECT id FROM derive.metric_def WHERE name = 'facebook.messages_count'")
        ).scalar_one()

        # Hand-computed trailing_90d baseline for the last pre-shift day (index 39, 0-based):
        # window = day_counts[0:39] (39 known prior days, all from the baseline pattern).
        window = day_counts[0:39]
        expected_median = statistics.median(window)
        expected_mad = statistics.median([abs(v - expected_median) for v in window]) * 1.4826
        last_pre_shift_day = connection.execute(
            text(
                "SELECT bucket_start FROM measure.metric_daily "
                "WHERE metric_id = :mid ORDER BY bucket_start OFFSET 39 LIMIT 1"
            ),
            {"mid": fb_metric_id},
        ).scalar_one()
        row = connection.execute(
            text(
                "SELECT center, spread, n_obs FROM signal.baseline "
                "WHERE metric_id = :mid AND bucket_start = :day AND kind = 'trailing_90d'"
            ),
            {"mid": fb_metric_id, "day": last_pre_shift_day},
        ).one()
        assert row.n_obs == 39
        assert row.center == pytest.approx(expected_median)
        assert row.spread == pytest.approx(expected_mad)

        # A change point should land near the real shift (day index 40) -- PELT with jump=5 may
        # not land exactly on 40, so allow a real tolerance window rather than an exact date.
        change_point_days = list(
            connection.execute(
                text("SELECT at_date FROM signal.change_point WHERE metric_id = :mid"), {"mid": fb_metric_id}
            ).scalars()
        )
        assert change_point_days, "expected at least one change point for the synthetic shift"
        first_day = connection.execute(
            text("SELECT MIN(bucket_start) FROM measure.metric_daily WHERE metric_id = :mid"),
            {"mid": fb_metric_id},
        ).scalar_one()
        offsets = [(d - first_day).days for d in change_point_days]
        assert any(30 <= offset <= 50 for offset in offsets), offsets

        # The shifted days should register as robust-z anomalies against the trailing baseline.
        anomaly_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM signal.anomaly WHERE metric_id = :mid AND z_score >= 3.0"
            ),
            {"mid": fb_metric_id},
        ).scalar_one()
        assert anomaly_count > 0


# --- exit test 5 (detected half): cross-source-family clustering into a candidate episode ------


def test_episode_detect_clusters_change_points_across_source_families(database_engine: Engine) -> None:
    baseline_pattern = [2, 3, 2, 4, 2, 3]
    fb_counts = _shift_day_counts(50, 40, baseline_pattern, 30)
    im_counts = _shift_day_counts(50, 42, baseline_pattern, 25)  # shift 2 days later -- within 21d

    _declare_contract(database_engine, "facebook", covered_start="2024-01-01", covered_end="2024-02-20")
    _import_events(database_engine, "facebook", "fb-cluster.json", _facebook_message_specs(fb_counts, start_ordinal=0))
    _declare_contract(database_engine, "imessages", covered_start="2024-01-01", covered_end="2024-02-20")
    _import_events(
        database_engine, "imessages", "im-cluster.json",
        _imessages_incoming_specs(im_counts, start_ordinal=0),
    )

    derive_sync(database_engine)
    derive_run(database_engine)
    derive_sync(database_engine)
    derive_run(database_engine, include_global=True)

    detected = list_episodes(database_engine, kind="detected")
    assert len(detected) >= 1
    episode = detected[0]
    assert episode["status"] == "candidate"
    sources_in_evidence = {cp["source"] for cp in episode["evidence"]["change_points"]}
    assert len(sources_in_evidence) >= 2


# --- exit test 5 (declared half): declare_episode round-trips through list_episodes ------------


def test_episode_declare_round_trips_through_list(database_engine: Engine) -> None:
    from datetime import date

    episode_id = declare_episode(
        database_engine, start=date(2024, 3, 1), end=date(2024, 3, 15), label="Spring trip", note="test"
    )
    declared = list_episodes(database_engine, kind="declared")
    assert any(e["id"] == episode_id and e["label"] == "Spring trip" for e in declared)
    assert all(e["status"] == "confirmed" for e in declared if e["id"] == episode_id)


def _insert_detected_candidate(engine: Engine, *, start, end) -> int:
    with engine.begin() as connection:
        return connection.execute(
            text(
                """
                INSERT INTO episode.episode
                    (kind, status, start_range_low, start_range_high, end_range_low,
                     end_range_high, evidence)
                VALUES ('detected', 'candidate', :start, :start, :end, :end, '{}'::jsonb)
                RETURNING id
                """
            ),
            {"start": start, "end": end},
        ).scalar_one()


def test_episode_confirm_sets_status_and_label(database_engine: Engine) -> None:
    from datetime import date

    from personal_data_lab.derive.derivers.episode import confirm_episode

    episode_id = _insert_detected_candidate(
        database_engine, start=date(2024, 1, 1), end=date(2024, 1, 10)
    )

    confirmed = confirm_episode(database_engine, episode_id, label="Job search", note="real")

    assert confirmed["status"] == "confirmed"
    assert confirmed["label"] == "Job search"
    assert confirmed["note"] == "real"
    # clear_partition only ever removes kind='detected' AND status='candidate' rows -- confirm
    # this survives as 'detected'/'confirmed', not silently reclassified.
    again = list_episodes(database_engine, kind="detected")
    assert any(e["id"] == episode_id and e["status"] == "confirmed" for e in again)


def test_confirmed_candidate_is_not_reproposed_on_redetection(database_engine: Engine) -> None:
    """Regression test for a real disposability-rebuild finding: re-running
    episode_detect_candidates after a candidate has been confirmed must not insert a duplicate
    'candidate' row for the same date range -- clear_partition deliberately never touches a
    confirmed row (see episode.py's module docstring), so detect_episode_candidates itself must
    skip ranges that are already decided, or a full rebuild (or any later re-run once new data
    reaches the same change points) silently duplicates every already-confirmed episode.
    """
    from personal_data_lab.derive.derivers.episode import confirm_episode, detect_episode_candidates

    baseline_pattern = [2, 3, 2, 4, 2, 3]
    fb_counts = _shift_day_counts(50, 40, baseline_pattern, 30)
    im_counts = _shift_day_counts(50, 42, baseline_pattern, 25)

    _declare_contract(database_engine, "facebook", covered_start="2024-01-01", covered_end="2024-02-20")
    _import_events(database_engine, "facebook", "fb-redetect.json", _facebook_message_specs(fb_counts, start_ordinal=0))
    _declare_contract(database_engine, "imessages", covered_start="2024-01-01", covered_end="2024-02-20")
    _import_events(
        database_engine, "imessages", "im-redetect.json",
        _imessages_incoming_specs(im_counts, start_ordinal=0),
    )

    derive_sync(database_engine)
    derive_run(database_engine)
    derive_sync(database_engine)
    derive_run(database_engine, include_global=True)

    detected = list_episodes(database_engine, kind="detected")
    assert len(detected) == 1
    confirm_episode(database_engine, detected[0]["id"], label="Confirmed period")

    # Re-run detection against the exact same underlying change points -- nothing about the raw
    # evidence changed, so the clustering result is identical to the first run's. Before the fix,
    # this would insert a second, duplicate 'candidate' row for the range already confirmed above.
    with database_engine.begin() as connection:
        candidates = detect_episode_candidates(connection)
    assert candidates == []

    still_detected = list_episodes(database_engine, kind="detected")
    assert len(still_detected) == 1
    assert still_detected[0]["status"] == "confirmed"


def test_episode_reject_sets_status_permanently(database_engine: Engine) -> None:
    from datetime import date

    from personal_data_lab.derive.derivers.episode import confirm_episode, reject_episode

    episode_id = _insert_detected_candidate(
        database_engine, start=date(2024, 2, 1), end=date(2024, 2, 5)
    )

    rejected = reject_episode(database_engine, episode_id, note="coincidence, not a real period")

    assert rejected["status"] == "rejected"
    assert rejected["note"] == "coincidence, not a real period"
    with pytest.raises(ValueError, match="already status='rejected'"):
        confirm_episode(database_engine, episode_id)


def test_episode_confirm_rejects_an_unknown_id(database_engine: Engine) -> None:
    from personal_data_lab.derive.derivers.episode import confirm_episode

    with pytest.raises(ValueError, match="no episode with id"):
        confirm_episode(database_engine, 999_999_999)


def test_episode_confirm_rejects_a_non_detected_kind(database_engine: Engine) -> None:
    from datetime import date

    from personal_data_lab.derive.derivers.episode import confirm_episode

    declared_id = declare_episode(
        database_engine, start=date(2024, 4, 1), end=date(2024, 4, 5), label="Already declared"
    )

    with pytest.raises(ValueError, match="not 'detected'"):
        confirm_episode(database_engine, declared_id)


# --- exit test 4: 1:1 interaction dynamics match a hand count; a group thread is excluded ------


def test_interaction_dynamics_1on1_matches_hand_count_and_group_excluded(database_engine: Engine) -> None:
    _link_facebook_owner(database_engine, "Ry Owner")
    _declare_contract(database_engine, "facebook", covered_start="2024-06-01", covered_end="2024-06-30")

    def _at(day: int, hour: int) -> datetime:
        return datetime(2024, 6, day, hour, 0, tzinfo=UTC)

    # A clean 1:1 thread: correspondent opens, owner replies same exchange (< 4h gap), then a
    # second exchange starts the next day (correspondent again).
    oneonone_specs = [
        {"occurred_at": _at(1, 9), "event_type": "message", "source_record_id": "oo-1", "conversation_id": "oneonone", "actor_identity": "Correspondent"},
        {"occurred_at": _at(1, 10), "event_type": "message", "source_record_id": "oo-2", "conversation_id": "oneonone", "actor_identity": "Ry Owner"},
        {"occurred_at": _at(2, 9), "event_type": "message", "source_record_id": "oo-3", "conversation_id": "oneonone", "actor_identity": "Correspondent"},
    ]
    # A group thread with 2 non-owner correspondents -- must produce no interaction_dynamics row
    # for either correspondent.
    group_specs = [
        {"occurred_at": _at(1, 9), "event_type": "message", "source_record_id": "gr-1", "conversation_id": "group", "actor_identity": "GroupA"},
        {"occurred_at": _at(1, 10), "event_type": "message", "source_record_id": "gr-2", "conversation_id": "group", "actor_identity": "GroupB"},
        {"occurred_at": _at(1, 11), "event_type": "message", "source_record_id": "gr-3", "conversation_id": "group", "actor_identity": "Ry Owner"},
    ]
    _import_events(database_engine, "facebook", "fb-interaction.json", oneonone_specs + group_specs)

    derive_sync(database_engine)
    derive_run(database_engine)
    derive_sync(database_engine)
    derive_run(database_engine, include_global=True)

    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT * FROM measure.interaction_dynamics "
                "WHERE source = 'facebook' AND correspondent = 'Correspondent'"
            )
        ).mappings().one()
        group_rows = connection.execute(
            text(
                "SELECT COUNT(*) FROM measure.interaction_dynamics "
                "WHERE correspondent IN ('GroupA', 'GroupB')"
            )
        ).scalar_one()

    assert row["messages_in"] == 2  # oo-1, oo-3
    assert row["messages_out"] == 1  # oo-2
    assert row["reciprocity_index"] == pytest.approx(1 / 2)
    # Two exchanges: (oo-1 -- correspondent-initiated), (oo-3, >4h after oo-2 -- also
    # correspondent-initiated). owner initiated 0 of 2.
    assert row["initiation_share"] == pytest.approx(0.0)
    # oo-1 -> oo-2 is a same-exchange sender switch 1 hour apart -- the only latency sample.
    assert row["median_response_latency_seconds"] == pytest.approx(3600.0)
    assert group_rows == 0


def test_facebook_owner_is_the_linked_identity_in_the_most_conversations(database_engine: Engine) -> None:
    from personal_data_lab.derive.derivers.interaction import _facebook_owner_identity

    base = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    specs = []
    for conv in range(3):
        for actor in ("Ry Owner", f"Friend {conv}"):
            specs.append(
                {
                    "occurred_at": base + timedelta(hours=conv),
                    "event_type": "message",
                    "source_record_id": f"owner-{conv}-{actor}",
                    "conversation_id": f"owner-conv-{conv}",
                    "actor_identity": actor,
                }
            )
    _import_events(database_engine, "facebook", "owner-test.json", specs)
    # A correspondent linked to a person *before* the owner -- the old unordered LIMIT 1 could
    # return either one.
    _link_facebook_owner(database_engine, "Friend 0")
    _link_facebook_owner(database_engine, "Ry Owner")

    with database_engine.connect() as connection:
        assert _facebook_owner_identity(connection) == "Ry Owner"
