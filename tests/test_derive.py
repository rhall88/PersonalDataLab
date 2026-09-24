from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Engine, text

from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.derive import REGISTRY, sync
from personal_data_lab.derive import rebuild as derive_rebuild
from personal_data_lab.derive import run as derive_run
from personal_data_lab.derive.partitions import GLOBAL_PARTITION_KEY
from personal_data_lab.derive.policy import (
    PolicyError,
    apply_egress_override,
    max_restriction,
    resolve_source_policy,
    sensitivity_union,
)
from personal_data_lab.derive.registry import (
    Deriver,
    DeriverRegistrationError,
    DeriverRegistry,
    _assert_acyclic,
)
from personal_data_lab.derive.scheduler import backfill_new_derivers
from personal_data_lab.derive.versioning import ensure_deriver_version, promote, version_hash
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
        # Per-run derive state, reset so each test starts from "nothing has ever been computed".
        # policy.source_policy and derive.deriver* are left alone -- standing configuration and
        # idempotent registrations, not per-test data.
        connection.execute(
            text(
                "TRUNCATE TABLE derive.derivation_run, derive.partition_state, "
                "derive.evidence_epoch RESTART IDENTITY CASCADE"
            )
        )
        connection.execute(text("UPDATE derive.change_cursor SET last_import_run_id = 0"))
        connection.execute(
            text("TRUNCATE TABLE measure.source_activity_daily, measure.source_activity_7d")
        )
    yield


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _import_event(
    engine: Engine, source: str, filename: str, occurred_at: datetime, event_id: str
) -> None:
    parsed = ParsedExport(
        source=source,
        parser_version="test-derive-v1",
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
    import_parsed_exports(engine, (parsed,))


def _noop_compute(connection, partition_key, epoch_id):
    raise AssertionError("never called")


def _noop_clear(connection, partition_key):
    raise AssertionError("never called")


# --- change feed + scheduling -------------------------------------------------------------


def test_sync_is_noop_on_an_empty_database(database_engine: Engine) -> None:
    outcome = sync(database_engine)
    assert outcome.is_noop
    assert outcome.epoch_id is None
    assert outcome.dirtied_local_keys == 0


def test_sync_dirties_local_and_propagates_across_the_month_boundary(database_engine: Engine) -> None:
    _import_event(database_engine, "google", "a.json", datetime(2024, 3, 15, tzinfo=UTC), "e1")

    outcome = sync(database_engine)

    assert not outcome.is_noop
    assert outcome.dirtied_local_keys == 1
    with database_engine.connect() as connection:
        dirty = set(
            connection.execute(
                text(
                    """
                    SELECT dv.deriver_name, ps.partition_key
                    FROM derive.partition_state ps
                    JOIN derive.deriver_version dv ON dv.id = ps.deriver_version_id
                    WHERE ps.state = 'dirty'
                    """
                )
            ).all()
        )
    # A change anywhere in March dirties March's own daily+7d partitions, and April's 7d
    # partition too -- April's first six days' trailing windows can reach back into March.
    assert dirty == {
        ("source_activity_daily", "google:2024-03"),
        ("source_activity_7d", "google:2024-03"),
        ("source_activity_7d", "google:2024-04"),
        ("measure_coverage", "google:2024-03"),
        ("measure_metric_daily", "google:2024-03"),
        ("measure_metric_daily_global", "GLOBAL"),
        ("signal_baseline", "GLOBAL"),
        ("signal_change_point", "GLOBAL"),
        ("signal_anomaly", "GLOBAL"),
        ("episode_detect_candidates", "GLOBAL"),
        ("measure_interaction_dynamics", "GLOBAL"),
        ("signal_interaction_state", "GLOBAL"),
        ("know_claim_evaluate", "GLOBAL"),
        # semantic_text_unit is HALO on both sides (lookback_halo="1 days",
        # lookahead_halo="1 days") -- as a root, the fix above expands a change anywhere in
        # March through that same halo against the whole month's boundary, touching Feb 29
        # (March 1 minus a day) and April 1 (March 31 plus a day) too, not just March itself.
        ("semantic_text_unit", "google:2024-02"),
        ("semantic_text_unit", "google:2024-03"),
        ("semantic_text_unit", "google:2024-04"),
        ("semantic_embedding_bge_small", "google:2024-02"),
        ("semantic_embedding_bge_small", "google:2024-03"),
        ("semantic_embedding_bge_small", "google:2024-04"),
        # media_transcribe_whisper is LOCAL (no halo), so just the one exact month, unlike
        # semantic_text_unit's HALO expansion above.
        ("media_transcribe_whisper", "google:2024-03"),
    }


def test_second_sync_with_nothing_new_is_a_noop(database_engine: Engine) -> None:
    _import_event(database_engine, "google", "a.json", datetime(2024, 3, 15, tzinfo=UTC), "e1")
    sync(database_engine)

    assert sync(database_engine).is_noop


# --- backfill_new_derivers: a deriver added after history already exists -------------------
#
# These exercise scheduler.backfill_new_derivers directly rather than through sync(), the same
# way the versioning tests below exercise ensure_deriver_version directly -- sync() always runs
# against the one shared, persistent REGISTRY (see test_the_real_registry_has_no_cycles...), so
# none of its derivers are ever "new" once the test database has run once, and mutating REGISTRY
# mid-suite would leak between tests. A fresh, uniquely-named DeriverRegistry sidesteps that.


def test_backfill_seeds_a_new_root_deriver_from_declared_contracts(database_engine: Engine) -> None:
    source = f"test_backfill_source_{uuid.uuid4().hex[:8]}"
    name = f"test_backfill_root_{uuid.uuid4().hex[:8]}"
    root = Deriver(
        name=name, compute=_noop_compute, clear_partition=_noop_clear,
        partition_class="LOCAL", final_inference="COMPUTED", description="",
        output_relation=f"measure.{name}",
    )
    registry = DeriverRegistry()
    registry.register(root)

    with database_engine.begin() as connection:
        registry.sync(connection)
        ensure_deriver_version(connection, root)
        connection.execute(
            text(
                """
                INSERT INTO declare.source_contract
                    (source, observable_kinds, covered_range_start, covered_range_end,
                     gap_semantics, default_timezone)
                VALUES (:source, ARRAY['event'], :start, :end, 'unknown', 'UTC')
                """
            ),
            {"source": source, "start": date(2023, 1, 10), "end": date(2023, 3, 5)},
        )

        seeded = backfill_new_derivers(connection, [root])

        # A root deriver's key space is every declared source's covered_range, not just this
        # test's own -- declare.source_contract is standing config other test modules also
        # declare into and never truncate (same convention as derive.deriver*), so this only
        # asserts our source's own three months are present, not the exact total.
        assert seeded[name] >= 3
        dirty = set(
            connection.execute(
                text(
                    """
                    SELECT ps.partition_key FROM derive.partition_state ps
                    JOIN derive.deriver_version dv ON dv.id = ps.deriver_version_id
                    WHERE dv.deriver_name = :name AND ps.state = 'dirty'
                    """
                ),
                {"name": name},
            ).scalars()
        )
        assert {f"{source}:2023-01", f"{source}:2023-02", f"{source}:2023-03"} <= dirty


def test_backfill_seeds_a_new_local_deriver_from_upstream_partition_state(
    database_engine: Engine,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    upstream_name = f"test_backfill_upstream_{suffix}"
    downstream_name = f"test_backfill_downstream_{suffix}"
    upstream = Deriver(
        name=upstream_name, compute=_noop_compute, clear_partition=_noop_clear,
        partition_class="LOCAL", final_inference="COMPUTED", description="",
        output_relation=f"measure.{upstream_name}",
    )
    downstream = Deriver(
        name=downstream_name, compute=_noop_compute, clear_partition=_noop_clear,
        partition_class="LOCAL", final_inference="COMPUTED", description="",
        output_relation=f"measure.{downstream_name}", inputs=(upstream_name,),
    )
    registry = DeriverRegistry()
    registry.register(upstream)
    registry.register(downstream)

    with database_engine.begin() as connection:
        registry.sync(connection)
        ensure_deriver_version(connection, upstream)
        ensure_deriver_version(connection, downstream)
        # Simulate upstream already having real history -- e.g. it shipped in an earlier phase
        # and has been computing for a while -- unrelated to whatever's currently dirty.
        upstream_version_id = connection.execute(
            text(
                "SELECT id FROM derive.deriver_version WHERE deriver_name = :n AND status = 'active'"
            ),
            {"n": upstream_name},
        ).scalar_one()
        for key in ("src:2024-01", "src:2024-02"):
            connection.execute(
                text(
                    """
                    INSERT INTO derive.partition_state
                        (deriver_version_id, partition_key, state, input_fingerprint, computed_at)
                    VALUES (:vid, :key, 'clean', :fp, CURRENT_TIMESTAMP)
                    """
                ),
                {"vid": upstream_version_id, "key": key, "fp": b"x"},
            )

        seeded = backfill_new_derivers(connection, [downstream])

        assert seeded == {downstream_name: 2}
        dirty = set(
            connection.execute(
                text(
                    """
                    SELECT ps.partition_key FROM derive.partition_state ps
                    JOIN derive.deriver_version dv ON dv.id = ps.deriver_version_id
                    WHERE dv.deriver_name = :name AND ps.state = 'dirty'
                    """
                ),
                {"name": downstream_name},
            ).scalars()
        )
        assert dirty == {"src:2024-01", "src:2024-02"}


def test_backfill_projects_upstream_keys_through_a_global_downstream(
    database_engine: Engine,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    upstream_name = f"test_backfill_global_upstream_{suffix}"
    downstream_name = f"test_backfill_global_downstream_{suffix}"
    upstream = Deriver(
        name=upstream_name, compute=_noop_compute, clear_partition=_noop_clear,
        partition_class="LOCAL", final_inference="COMPUTED", description="",
        output_relation=f"measure.{upstream_name}",
    )
    downstream = Deriver(
        name=downstream_name, compute=_noop_compute, clear_partition=_noop_clear,
        partition_class="GLOBAL", final_inference="COMPUTED", description="",
        output_relation=f"measure.{downstream_name}", inputs=(upstream_name,),
    )
    registry = DeriverRegistry()
    registry.register(upstream)
    registry.register(downstream)

    with database_engine.begin() as connection:
        registry.sync(connection)
        ensure_deriver_version(connection, upstream)
        ensure_deriver_version(connection, downstream)
        upstream_version_id = connection.execute(
            text(
                "SELECT id FROM derive.deriver_version WHERE deriver_name = :n AND status = 'active'"
            ),
            {"n": upstream_name},
        ).scalar_one()
        connection.execute(
            text(
                """
                INSERT INTO derive.partition_state
                    (deriver_version_id, partition_key, state, input_fingerprint, computed_at)
                VALUES (:vid, 'src:2024-05', 'clean', :fp, CURRENT_TIMESTAMP)
                """
            ),
            {"vid": upstream_version_id, "fp": b"x"},
        )

        seeded = backfill_new_derivers(connection, [downstream])

        assert seeded == {downstream_name: 1}
        dirty = set(
            connection.execute(
                text(
                    """
                    SELECT ps.partition_key FROM derive.partition_state ps
                    JOIN derive.deriver_version dv ON dv.id = ps.deriver_version_id
                    WHERE dv.deriver_name = :name AND ps.state = 'dirty'
                    """
                ),
                {"name": downstream_name},
            ).scalars()
        )
        assert dirty == {GLOBAL_PARTITION_KEY}


def test_backfill_of_a_non_root_deriver_with_no_upstream_history_seeds_nothing(
    database_engine: Engine,
) -> None:
    # The whole chain is new at once (e.g. a fresh install) and its root upstream has never
    # computed anything yet -- there is no partition space to adopt, so backfill is a no-op
    # rather than inventing one. Deliberately does not touch declare.source_contract, which
    # other test modules declare into and never truncate -- see the root-deriver test above.
    suffix = uuid.uuid4().hex[:8]
    upstream_name = f"test_backfill_empty_upstream_{suffix}"
    downstream_name = f"test_backfill_empty_downstream_{suffix}"
    upstream = Deriver(
        name=upstream_name, compute=_noop_compute, clear_partition=_noop_clear,
        partition_class="LOCAL", final_inference="COMPUTED", description="",
        output_relation=f"measure.{upstream_name}",
    )
    downstream = Deriver(
        name=downstream_name, compute=_noop_compute, clear_partition=_noop_clear,
        partition_class="LOCAL", final_inference="COMPUTED", description="",
        output_relation=f"measure.{downstream_name}", inputs=(upstream_name,),
    )
    registry = DeriverRegistry()
    registry.register(upstream)
    registry.register(downstream)

    with database_engine.begin() as connection:
        registry.sync(connection)
        ensure_deriver_version(connection, upstream)
        ensure_deriver_version(connection, downstream)

        assert backfill_new_derivers(connection, [downstream]) == {downstream_name: 0}


# --- worker: compute, write, skip-unchanged ------------------------------------------------


def test_run_computes_daily_and_7d_rows(database_engine: Engine) -> None:
    _import_event(database_engine, "google", "a.json", datetime(2024, 3, 15, tzinfo=UTC), "e1")
    sync(database_engine)

    outcomes = derive_run(database_engine)

    assert {o.status for o in outcomes} == {"succeeded"}
    assert {(o.deriver_name, o.partition_key) for o in outcomes} == {
        ("source_activity_daily", "google:2024-03"),
        ("source_activity_7d", "google:2024-03"),
        ("source_activity_7d", "google:2024-04"),
        ("measure_coverage", "google:2024-03"),
        ("measure_metric_daily", "google:2024-03"),
        # semantic_text_unit/semantic_embedding_bge_small are LOCAL/HALO, not GLOBAL, so they
        # run in a plain (non --global) derive_run too -- see the dirty-set test above for why
        # three months each, not just March.
        ("semantic_text_unit", "google:2024-02"),
        ("semantic_text_unit", "google:2024-03"),
        ("semantic_text_unit", "google:2024-04"),
        ("semantic_embedding_bge_small", "google:2024-02"),
        ("semantic_embedding_bge_small", "google:2024-03"),
        ("semantic_embedding_bge_small", "google:2024-04"),
        # media_transcribe_whisper is LOCAL (no halo), so just the one exact month, unlike
        # semantic_text_unit's HALO expansion above.
        ("media_transcribe_whisper", "google:2024-03"),
    }

    with database_engine.connect() as connection:
        daily = connection.execute(
            text(
                "SELECT active_count, tombstoned_count FROM measure.source_activity_daily "
                "WHERE source = 'google' AND day = '2024-03-15' AND object_kind = 'event'"
            )
        ).one()
        assert (daily.active_count, daily.tombstoned_count) == (1, 0)

        window = connection.execute(
            text(
                "SELECT active_count FROM measure.source_activity_7d "
                "WHERE source = 'google' AND day = '2024-03-15' AND object_kind = 'event'"
            )
        ).scalar_one()
        assert window == 1

        # April's own trailing-7-day window never reaches back to March 15th (the earliest it
        # could is March 26, for April 1st), so no daily row falls inside it at all -- the
        # deriver writes no "event" rows for April whatsoever, rather than a row of zeros.
        april_first = connection.execute(
            text(
                "SELECT active_count FROM measure.source_activity_7d "
                "WHERE source = 'google' AND day = '2024-04-01' AND object_kind = 'event'"
            )
        ).scalar()
        assert april_first is None


def test_run_with_nothing_dirty_does_nothing(database_engine: Engine) -> None:
    assert derive_run(database_engine) == []


def test_redirtying_without_a_data_change_is_skipped_unchanged(database_engine: Engine) -> None:
    _import_event(database_engine, "google", "a.json", datetime(2024, 3, 15, tzinfo=UTC), "e1")
    sync(database_engine)
    derive_run(database_engine)

    with database_engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE derive.partition_state ps
                SET state = 'dirty'
                FROM derive.deriver_version dv
                WHERE ps.deriver_version_id = dv.id AND dv.deriver_name = 'source_activity_daily'
                """
            )
        )

    outcomes = derive_run(database_engine)
    assert [o.status for o in outcomes] == ["skipped_unchanged"]
    assert outcomes[0].rows_written is None


def test_rebuild_reproduces_identical_output(database_engine: Engine) -> None:
    _import_event(database_engine, "google", "a.json", datetime(2024, 3, 15, tzinfo=UTC), "e1")
    sync(database_engine)
    derive_run(database_engine)

    with database_engine.connect() as connection:
        before = connection.execute(
            text(
                "SELECT source, day, object_kind, active_count, tombstoned_count, versions "
                "FROM measure.source_activity_daily ORDER BY source, day, object_kind"
            )
        ).all()

    dirtied = derive_rebuild(database_engine, deriver_name="source_activity_daily")
    assert dirtied == 1
    derive_run(database_engine)

    with database_engine.connect() as connection:
        after = connection.execute(
            text(
                "SELECT source, day, object_kind, active_count, tombstoned_count, versions "
                "FROM measure.source_activity_daily ORDER BY source, day, object_kind"
            )
        ).all()
    assert before == after


# --- registry: cycle rejection ---------------------------------------------------------------


def test_dependency_cycle_is_rejected() -> None:
    # register() itself can never be asked to form a cycle -- it refuses an unknown upstream and
    # refuses to re-register an existing name, so a real caller can't back into a loop through
    # the public API. _assert_acyclic is the guard that would catch one if that ever changed;
    # exercise it directly against a hand-built pair of mutually-dependent derivers.
    a = Deriver(
        name="a", compute=_noop_compute, clear_partition=_noop_clear, partition_class="LOCAL",
        final_inference="COMPUTED", description="", output_relation="measure.a", inputs=("b",),
    )
    b = Deriver(
        name="b", compute=_noop_compute, clear_partition=_noop_clear, partition_class="LOCAL",
        final_inference="COMPUTED", description="", output_relation="measure.b", inputs=("a",),
    )
    with pytest.raises(DeriverRegistrationError):
        _assert_acyclic({"a": a, "b": b})


def test_register_rejects_an_unknown_upstream_dependency() -> None:
    registry = DeriverRegistry()
    orphan = Deriver(
        name="downstream", compute=_noop_compute, clear_partition=_noop_clear,
        partition_class="LOCAL", final_inference="COMPUTED", description="",
        output_relation="measure.downstream", inputs=("does_not_exist",),
    )
    with pytest.raises(DeriverRegistrationError):
        registry.register(orphan)


def test_register_rejects_a_halo_set_without_halo_partition_class() -> None:
    registry = DeriverRegistry()
    misconfigured = Deriver(
        name="bad", compute=_noop_compute, clear_partition=_noop_clear, partition_class="LOCAL",
        final_inference="COMPUTED", description="", output_relation="measure.bad",
        lookback_halo="6 days",
    )
    with pytest.raises(DeriverRegistrationError):
        registry.register(misconfigured)


# --- policy: egress lattice -------------------------------------------------------------------


def test_max_restriction_orders_by_declaration_not_alphabetically() -> None:
    assert max_restriction("external_model_allowed") == "external_model_allowed"
    assert max_restriction("external_model_allowed", "redactable") == "redactable"
    assert max_restriction("redactable", "local_only", "external_model_allowed") == "local_only"


def test_sensitivity_union_dedupes_preserving_first_seen_order() -> None:
    assert sensitivity_union(["private"], ["intimate", "private"], ["financial"]) == [
        "private",
        "intimate",
        "financial",
    ]


def test_resolve_source_policy_reads_seeded_defaults(database_engine: Engine) -> None:
    with database_engine.connect() as connection:
        sensitivity, egress = resolve_source_policy(connection, "imessages")
    assert sensitivity == ["intimate"]
    assert egress == "local_only"


def test_resolve_source_policy_raises_for_an_unknown_source(database_engine: Engine) -> None:
    with database_engine.connect() as connection, pytest.raises(PolicyError):
        resolve_source_policy(connection, "not-a-real-source")


def test_egress_override_can_only_loosen(database_engine: Engine) -> None:
    with database_engine.begin() as connection:
        declaration_id = connection.execute(
            text(
                "INSERT INTO declare.declaration (kind, payload) "
                "VALUES ('egress_override', '{}'::jsonb) RETURNING id"
            )
        ).scalar_one()
        connection.execute(
            text(
                """
                INSERT INTO policy.egress_override
                    (object_ref, from_level, to_level, declaration_id)
                VALUES ('test-object', 'local_only', 'redactable', :declaration_id)
                """
            ),
            {"declaration_id": declaration_id},
        )
        loosened = apply_egress_override(connection, "test-object", "local_only")
        unaffected = apply_egress_override(connection, "some-other-object", "local_only")
    assert loosened == "redactable"
    assert unaffected == "local_only"


# --- versioning: hash + shadow/promote ---------------------------------------------------------


def test_version_hash_changes_when_params_change() -> None:
    base = Deriver(
        name="x", compute=_noop_compute, clear_partition=_noop_clear, partition_class="LOCAL",
        final_inference="COMPUTED", description="", output_relation="measure.x",
        params={"threshold": 1},
    )
    changed = Deriver(
        name="x", compute=_noop_compute, clear_partition=_noop_clear, partition_class="LOCAL",
        final_inference="COMPUTED", description="", output_relation="measure.x",
        params={"threshold": 2},
    )
    assert version_hash(base) != version_hash(changed)


def test_ensure_deriver_version_then_promote(database_engine: Engine) -> None:
    # A fresh name per test run -- derive.deriver_version rows are standing, idempotent state
    # (clean_database deliberately leaves them alone; see its comment), so reusing a name across
    # repeated runs of this suite would inherit a previous run's promotion/retirement instead of
    # starting from "no versions exist yet".
    name = f"test_versioning_deriver_{uuid.uuid4().hex[:8]}"
    registry = DeriverRegistry()
    v1 = Deriver(
        name=name, compute=_noop_compute, clear_partition=_noop_clear,
        partition_class="LOCAL", final_inference="COMPUTED", description="",
        output_relation=f"measure.{name}", code_version="1",
    )
    registry.register(v1)

    with database_engine.begin() as connection:
        registry.sync(connection)
        v1_id = ensure_deriver_version(connection, v1)

        v2 = Deriver(
            name=name, compute=_noop_compute, clear_partition=_noop_clear,
            partition_class="LOCAL", final_inference="COMPUTED", description="",
            output_relation=f"measure.{name}", code_version="2",
        )
        v2_id = ensure_deriver_version(connection, v2)
        assert v1_id != v2_id

        statuses = dict(
            connection.execute(
                text("SELECT id, status FROM derive.deriver_version WHERE deriver_name = :name"),
                {"name": name},
            ).all()
        )
        assert statuses[v1_id] == "active"
        assert statuses[v2_id] == "shadow"

        promote(connection, name, version_hash(v2).hex())

        statuses = dict(
            connection.execute(
                text("SELECT id, status FROM derive.deriver_version WHERE deriver_name = :name"),
                {"name": name},
            ).all()
        )
        assert statuses[v1_id] == "retired"
        assert statuses[v2_id] == "active"


def test_promoting_a_retired_version_is_rejected(database_engine: Engine) -> None:
    name = f"test_repromote_deriver_{uuid.uuid4().hex[:8]}"
    registry = DeriverRegistry()
    v1 = Deriver(
        name=name, compute=_noop_compute, clear_partition=_noop_clear,
        partition_class="LOCAL", final_inference="COMPUTED", description="",
        output_relation=f"measure.{name}", code_version="1",
    )
    registry.register(v1)
    v2 = Deriver(
        name=name, compute=_noop_compute, clear_partition=_noop_clear,
        partition_class="LOCAL", final_inference="COMPUTED", description="",
        output_relation=f"measure.{name}", code_version="2",
    )

    with database_engine.begin() as connection:
        registry.sync(connection)
        ensure_deriver_version(connection, v1)
        ensure_deriver_version(connection, v2)
        promote(connection, name, version_hash(v2).hex())

        from personal_data_lab.derive.versioning import VersioningError

        with pytest.raises(VersioningError):
            promote(connection, name, version_hash(v1).hex())


# --- registry sanity: REGISTRY is what the CLI/worker actually use -----------------------------


def test_the_real_registry_has_no_cycles_and_both_phase0_derivers() -> None:
    names = {d.name for d in REGISTRY}
    assert {"source_activity_daily", "source_activity_7d"} <= names
