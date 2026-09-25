"""Exit tests 1-4 from PHASE5_IMPLEMENTATION_PLAN.md (exit test 5, the real-database rollout, is
done live against the real canonical database, not here). Mirrors tests/test_phase4.py's
fixtures, extended to also reset Phase 5's know.claim table between tests.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text

from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.derive import run as derive_run
from personal_data_lab.derive import sync as derive_sync
from personal_data_lab.derive.derivers.claims import claim_why, list_claims
from personal_data_lab.derive.scheduler import mark_dirty
from personal_data_lab.models import NormalizedEvent, ParsedExport

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
        connection.execute(text("TRUNCATE TABLE know.claim RESTART IDENTITY CASCADE"))
    yield


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _settle_one_dummy_event(engine: Engine) -> None:
    """Import one trivial event and run the whole pipeline (including --global) to completion,
    so every real deriver -- including know_claim_evaluate -- has a real derive.evidence_epoch,
    an active derive.deriver_version, and a 'clean' GLOBAL partition to start from. Exit tests
    2-4 then inject synthetic signal.anomaly/episode.episode rows directly and re-dirty only
    know_claim_evaluate (never the whole graph again), so nothing here risks a sibling GLOBAL
    deriver's own clear-and-replace wiping out the synthetic rows before know_claim_evaluate
    reads them -- see claims.py's own module docstring on GLOBAL-sibling run-order.
    """
    parsed = ParsedExport(
        source="facebook",
        parser_version="test-phase5-v1",
        filename="settle.json",
        original_path="settle.json",
        sha256=_sha("settle.json"),
        size_bytes=1,
        events=(
            NormalizedEvent(
                occurred_at=datetime(2024, 1, 1, tzinfo=UTC),
                source="facebook",
                event_type="message",
                source_record_namespace="facebook/event",
                source_record_id="settle-1",
            ),
        ),
    )
    import_parsed_exports(engine, (parsed,))
    derive_sync(engine)
    derive_run(engine)
    derive_run(engine, include_global=True)


def _rerun_claims_only(engine: Engine) -> None:
    with engine.begin() as connection:
        mark_dirty(connection, "know_claim_evaluate", {"GLOBAL"})
    derive_run(engine, include_global=True)


def test_sync_registers_know_claim_evaluate(database_engine: Engine) -> None:
    derive_sync(database_engine)
    with database_engine.connect() as connection:
        row = connection.execute(
            text("SELECT partition_class FROM derive.deriver WHERE name = 'know_claim_evaluate'")
        ).first()
    assert row is not None
    assert row.partition_class == "GLOBAL"


def test_communication_change_claim_matches_the_anomaly(database_engine: Engine) -> None:
    _settle_one_dummy_event(database_engine)

    with database_engine.begin() as connection:
        epoch_id = connection.execute(text("SELECT MAX(id) FROM derive.evidence_epoch")).scalar_one()
        metric_id = connection.execute(
            text(
                "INSERT INTO derive.metric_def (name, description, unit) "
                "VALUES ('test.synthetic_count', 'synthetic', 'count') RETURNING id"
            )
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO derive.metric_dependency (metric_id, source, object_kind) "
                "VALUES (:mid, 'facebook', 'event')"
            ),
            {"mid": metric_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO measure.coverage
                    (source, bucket_start, grain, object_kind, expected, observed, completeness,
                     computed_epoch_id)
                VALUES ('facebook', '2016-01-20', 'day', 'event', 1, 106, 1.0, :epoch)
                """
            ),
            {"epoch": epoch_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO signal.anomaly
                    (metric_id, bucket_start, observed, baseline_kind, expected_low,
                     expected_high, z_score, method)
                VALUES (:mid, '2016-01-20', 106, 'trailing_90d', -1.7, 2.7, 142.3, 'robust_z')
                """
            ),
            {"mid": metric_id},
        )

    _rerun_claims_only(database_engine)

    claims = list_claims(database_engine, template="communication_change")
    assert len(claims) == 1
    claim = claims[0]
    assert claim["class"] == "STATISTICAL"
    assert "test.synthetic_count" in claim["statement"]
    assert "2016-01-20" in claim["statement"]
    confidence = claim["confidence"]
    assert confidence["statistical_strength"] == pytest.approx(142.3)
    assert confidence["source_independence"] == 1
    assert confidence["coverage"] == pytest.approx(1.0)
    assert "identity" not in confidence


def test_episode_evidence_claims_by_kind_and_status(database_engine: Engine) -> None:
    _settle_one_dummy_event(database_engine)

    detected_confirmed_evidence = {
        "change_points": [
            {"metric": "facebook.messages_count", "source": "facebook", "date": "2016-06-28"},
            {"metric": "google.mail_messages_count", "source": "google", "date": "2016-07-10"},
        ]
    }
    with database_engine.begin() as connection:
        for kind, status, label, note, evidence in (
            ("detected", "confirmed", "Daughter born", None, detected_confirmed_evidence),
            ("declared", "confirmed", "A declared period", None, {}),
            ("detected", "candidate", None, None, detected_confirmed_evidence),
            ("detected", "rejected", None, "coincidence, not real", detected_confirmed_evidence),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO episode.episode
                        (kind, status, start_range_low, start_range_high, end_range_low,
                         end_range_high, label, note, evidence)
                    VALUES (:kind, :status, '2016-06-28', '2016-06-28', '2016-12-15',
                            '2016-12-15', :label, :note, CAST(:evidence AS jsonb))
                    """
                ),
                {
                    "kind": kind, "status": status, "label": label, "note": note,
                    "evidence": json.dumps(evidence),
                },
            )

    _rerun_claims_only(database_engine)

    claims = list_claims(database_engine, template="episode_evidence")
    # Exactly 3 claims: candidate produces none (invariant 38 -- unreviewed stays unreviewed).
    assert len(claims) == 3

    detected = next(c for c in claims if c["class"] == "STATISTICAL" and c["subject"]["episode_status"] == "confirmed")
    declared = next(c for c in claims if c["class"] == "DECLARED")
    rejected = next(c for c in claims if c["subject"]["episode_status"] == "rejected")

    assert detected["statement"] == "Daughter born"
    assert detected["confidence"]["source_independence"] == 2
    assert detected["confidence"]["statistical_strength"] == 2
    assert len(detected["evidence"]["change_points"]) == 2

    assert declared["statement"] == "A declared period"
    assert declared["confidence"] == {}
    assert declared["evidence"]["change_points"] == []

    assert rejected["class"] == "STATISTICAL"
    assert "rejected" in rejected["statement"]
    assert "coincidence, not real" in rejected["statement"]


def test_relationship_state_shift_pairs_consecutive_states_only(database_engine: Engine) -> None:
    """Added 2026-09-25 (ROADMAP.md item 3): a correspondent with a single state produces no
    claim (no transition to report yet); one with two states produces exactly one
    relationship_state_shift claim for that consecutive pair.
    """
    _settle_one_dummy_event(database_engine)

    with database_engine.begin() as connection:
        for correspondent, state_start, state_end, in_msgs, out_msgs, reciprocity in (
            ("Solo Friend", "2018-01-01", "2020-01-01", 5.0, 5.0, 0.5),
            ("Fading Friend", "2015-01-01", "2018-01-01", 10.0, 8.0, 0.6),
            ("Fading Friend", "2018-01-01", "2021-01-01", 2.0, 0.5, 0.1),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO signal.interaction_state
                        (source, correspondent, state_start, state_end, mean_messages_in,
                         mean_messages_out, mean_reciprocity_index, n_months)
                    VALUES ('facebook', :correspondent, :state_start, :state_end, :in_msgs,
                            :out_msgs, :reciprocity, 24)
                    """
                ),
                {
                    "correspondent": correspondent, "state_start": state_start,
                    "state_end": state_end, "in_msgs": in_msgs, "out_msgs": out_msgs,
                    "reciprocity": reciprocity,
                },
            )

    _rerun_claims_only(database_engine)

    claims = list_claims(database_engine, template="relationship_state_shift")
    assert len(claims) == 1
    claim = claims[0]
    assert claim["subject"]["correspondent"] == "Fading Friend"
    assert claim["class"] == "STATISTICAL"
    assert "decreased" in claim["statement"]
    assert claim["confidence"]["statistical_strength"] == pytest.approx(0.5)
    assert claim["confidence"]["source_independence"] == 1
    assert claim["evidence"]["before"]["state_start"] == "2015-01-01"
    assert claim["evidence"]["after"]["state_start"] == "2018-01-01"

    why = claim_why(database_engine, claim["id"])
    assert len(why["evidence_detail"]) == 2
    before_detail, after_detail = why["evidence_detail"]
    assert before_detail["source_table"] == "signal.interaction_state"
    assert before_detail["refetched"]["mean_reciprocity_index"] == pytest.approx(0.6)
    assert after_detail["refetched"]["mean_reciprocity_index"] == pytest.approx(0.1)


def test_episode_coverage_averages_the_range_not_the_worst_single_day(
    database_engine: Engine,
) -> None:
    """Real weakness the 2026-09-24 Phase 5 rollout found and this fix corrects: a strict minimum
    across only the literal change-point dates meant one naturally sparse day dragged a whole
    multi-day episode's coverage to 0.0, even when the rest of its span was well covered. This
    proves the fix is a genuine mean over the episode's full range, not still a hidden floor.
    """
    _settle_one_dummy_event(database_engine)

    with database_engine.begin() as connection:
        epoch_id = connection.execute(text("SELECT MAX(id) FROM derive.evidence_epoch")).scalar_one()
        for day, completeness in (("2020-01-01", 1.0), ("2020-01-15", 0.0)):
            connection.execute(
                text(
                    """
                    INSERT INTO measure.coverage
                        (source, bucket_start, grain, object_kind, expected, observed,
                         completeness, computed_epoch_id)
                    VALUES ('facebook', :day, 'day', 'event', 1, :observed, :completeness, :epoch)
                    """
                ),
                {
                    "day": day, "observed": int(completeness), "completeness": completeness,
                    "epoch": epoch_id,
                },
            )
        connection.execute(
            text(
                """
                INSERT INTO episode.episode
                    (kind, status, start_range_low, start_range_high, end_range_low,
                     end_range_high, evidence)
                VALUES ('detected', 'confirmed', '2020-01-01', '2020-01-01', '2020-01-15',
                        '2020-01-15', CAST(:evidence AS jsonb))
                """
            ),
            {
                "evidence": json.dumps(
                    {
                        "change_points": [
                            # Deliberately cites only the sparse (completeness=0.0) day -- the
                            # old min-over-literal-days logic would report 0.0 here.
                            {"metric": "facebook.messages_count", "source": "facebook",
                             "date": "2020-01-15"},
                        ]
                    }
                )
            },
        )

    _rerun_claims_only(database_engine)

    [claim] = [
        c for c in list_claims(database_engine, template="episode_evidence")
        if c["evidence"]["change_points"][0]["date"] == "2020-01-15"
    ]
    # Mean of 1.0 (Jan 1) and 0.0 (Jan 15) across the episode's full range, not the 0.0 floor
    # a strict minimum over just the cited day would have produced.
    assert claim["confidence"]["coverage"] == pytest.approx(0.5)


def test_claims_why_refetches_every_cited_row_independently(database_engine: Engine) -> None:
    _settle_one_dummy_event(database_engine)

    with database_engine.begin() as connection:
        epoch_id = connection.execute(text("SELECT MAX(id) FROM derive.evidence_epoch")).scalar_one()
        metric_id = connection.execute(
            text(
                "INSERT INTO derive.metric_def (name, description, unit) "
                "VALUES ('test.why_count', 'synthetic', 'count') RETURNING id"
            )
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO measure.coverage "
                "(source, bucket_start, grain, object_kind, expected, observed, completeness, "
                "computed_epoch_id) VALUES ('facebook', '2020-05-01', 'day', 'event', 1, 3, 1.0, :epoch)"
            ),
            {"epoch": epoch_id},
        )
        connection.execute(
            text(
                "INSERT INTO signal.anomaly "
                "(metric_id, bucket_start, observed, baseline_kind, expected_low, expected_high, "
                "z_score, method) VALUES (:mid, '2020-05-01', 3, 'trailing_90d', -1, 1, 4.0, 'robust_z')"
            ),
            {"mid": metric_id},
        )

    _rerun_claims_only(database_engine)

    [claim] = list_claims(database_engine, template="communication_change")
    report = claim_why(database_engine, claim["id"])

    assert report["claim"]["id"] == claim["id"]
    assert len(report["evidence_detail"]) == 1
    detail = report["evidence_detail"][0]
    assert detail["source_table"] == "signal.anomaly"
    assert detail["refetched"] is not None
    assert detail["refetched"]["z_score"] == pytest.approx(4.0)
    assert "know_claim_evaluate" in report["deriver_version_state"]
    assert "signal_anomaly" in report["deriver_version_state"]
    assert all(h is not None for h in report["deriver_version_state"].values())

    with pytest.raises(ValueError, match="no claim with id"):
        claim_why(database_engine, 999_999_999)
