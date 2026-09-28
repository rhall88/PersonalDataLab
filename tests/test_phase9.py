"""Exit tests from PHASE9_TOPIC_CLUSTERING_IMPLEMENTATION_PLAN.md. Real-database rollout (the
plan's own exit criterion, since this phase needs no model/rented hardware) is done live against
the real canonical database, not here -- see PROJECT_STATE.md's "Phase 9" rollout section.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime

import numpy as np
import pytest
from sklearn.feature_extraction.text import TfidfVectorizer
from sqlalchemy import Engine, text

from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.derive import run as derive_run
from personal_data_lab.derive import sync as derive_sync
from personal_data_lab.derive.derivers.clustering import (
    _clear_topic_cluster_assignment,
    _cluster,
    _label_cluster,
)
from personal_data_lab.derive.scheduler import mark_dirty
from personal_data_lab.derive.versioning import active_version_id
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
                "derive.deriver_version, derive.evidence_epoch RESTART IDENTITY CASCADE"
            )
        )
        connection.execute(text("UPDATE derive.change_cursor SET last_import_run_id = 0"))
        connection.execute(
            text(
                "TRUNCATE TABLE semantic.text_unit, semantic.embedding_bge_small_en_v15, "
                "semantic.topic_cluster_assignment RESTART IDENTITY CASCADE"
            )
        )
    yield


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _seed_one_real_epoch(engine: Engine) -> int:
    """A real import_run + derive_sync() is the only way this schema lets an evidence_epoch (or
    a derive.deriver_version row for semantic_topic_cluster) come to exist -- reused here purely
    to get a valid epoch_id/registered-deriver state before hand-inserting synthetic vectors, the
    same real-plumbing-first approach test_phase6.py's own fixtures already use.
    """
    parsed = ParsedExport(
        source="google",
        parser_version="test-phase9-v1",
        filename="seed.json",
        original_path="seed.json",
        sha256=_sha("seed.json"),
        size_bytes=1,
        events=(
            NormalizedEvent(
                occurred_at=datetime(2024, 1, 1, tzinfo=UTC),
                source="google",
                event_type="mail_message",
                source_record_namespace="google/event",
                source_record_id="seed-1",
                text="seed event, not itself part of any cluster",
            ),
        ),
    )
    import_parsed_exports(engine, (parsed,))
    derive_sync(engine)
    # Settle every LOCAL/HALO partition the seed event's own backfill dirtied (semantic_text_unit
    # would otherwise reconstruct its January partition from real events on the *next* run this
    # test triggers too, which is harmless -- but running it now, before any synthetic rows exist,
    # keeps this helper's job legible: real plumbing first, synthetic data second, no interleaving).
    derive_run(engine, include_global=False)
    with engine.connect() as connection:
        return connection.execute(text("SELECT MAX(id) FROM derive.evidence_epoch")).scalar_one()


def _insert_text_unit(connection, source: str, text_body: str, started_at: datetime, epoch_id: int) -> int:
    return connection.execute(
        text(
            """
            INSERT INTO semantic.text_unit
                (source, event_ids, started_at, ended_at, text, computed_epoch_id)
            VALUES (:source, ARRAY[1]::BIGINT[], :started_at, :started_at, :text_body, :epoch_id)
            RETURNING id
            """
        ),
        {"source": source, "started_at": started_at, "text_body": text_body, "epoch_id": epoch_id},
    ).scalar_one()


def _vector_literal(values) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def _insert_embedding(connection, text_unit_id: int, vector: np.ndarray) -> None:
    connection.execute(
        text(
            "INSERT INTO semantic.embedding_bge_small_en_v15 (text_unit_id, vector) "
            "VALUES (:tid, :vec)"
        ),
        {"tid": text_unit_id, "vec": _vector_literal(vector)},
    )


def _seed_two_blobs_and_noise(engine: Engine, epoch_id: int) -> None:
    """30 real text units clustered tightly around one direction ("lease"/"landlord"/"apartment"
    vocabulary), 30 around a distinct direction ("revenue"/"forecast"/"quarterly" vocabulary), and
    5 scattered points sharing no real vocabulary with either -- real geometric separation (in a
    384-dim space, matching the real embedding table's own column width) and real, distinguishable
    text content, per the plan's own exit test 2/4 wording.
    """
    rng = np.random.default_rng(42)
    dim = 384
    base_a = np.zeros(dim)
    base_a[0] = 1.0
    base_b = np.zeros(dim)
    base_b[1] = 1.0

    lease_texts = [
        f"The lease for the apartment renews in {i} months, landlord wants a decision soon."
        for i in range(30)
    ]
    revenue_texts = [
        f"Quarterly revenue forecast update number {i}: sales are tracking above plan."
        for i in range(30)
    ]
    noise_texts = [
        "Completely unrelated scattered thought with no shared vocabulary at all whatsoever whichever",
        "Another orphan sentence about something totally different from either topic here now",
        "A third stray remark sharing nothing meaningful with the two real clusters above either",
        "Random miscellaneous filler content that does not belong to any real group of units",
        "Yet another one-off comment isolated from every other real text unit in this fixture",
    ]

    with engine.begin() as connection:
        for i, body in enumerate(lease_texts):
            vec = base_a + rng.normal(scale=0.01, size=dim)
            tid = _insert_text_unit(connection, "google", body, datetime(2024, 2, 1 + (i % 27), tzinfo=UTC), epoch_id)
            _insert_embedding(connection, tid, vec)
        for i, body in enumerate(revenue_texts):
            vec = base_b + rng.normal(scale=0.01, size=dim)
            tid = _insert_text_unit(connection, "google", body, datetime(2024, 2, 1 + (i % 27), tzinfo=UTC), epoch_id)
            _insert_embedding(connection, tid, vec)
        # Explicit, far-apart coordinate directions (not a random draw) -- guarantees each noise
        # point sits nowhere near base_a/base_b or each other, deterministically, rather than
        # leaving it to chance whether a random 384-dim direction happens to land close to one of
        # the two real basis vectors on a given run.
        noise_axes = (10, 60, 120, 200, 300)
        for axis, body in zip(noise_axes, noise_texts):
            vec = np.zeros(dim)
            vec[axis] = 1.0
            tid = _insert_text_unit(connection, "google", body, datetime(2024, 2, 1 + axis % 27, tzinfo=UTC), epoch_id)
            _insert_embedding(connection, tid, vec)


def test_sync_registers_semantic_topic_cluster_as_statistical_global(database_engine: Engine) -> None:
    _seed_one_real_epoch(database_engine)
    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT partition_class, final_inference FROM derive.deriver "
                "WHERE name = 'semantic_topic_cluster'"
            )
        ).one()
    assert (row.partition_class, row.final_inference) == ("GLOBAL", "STATISTICAL")


def test_two_separated_blobs_cluster_with_noise_group(database_engine: Engine) -> None:
    epoch_id = _seed_one_real_epoch(database_engine)
    _seed_two_blobs_and_noise(database_engine, epoch_id)

    outcomes = derive_run(database_engine, include_global=True)
    topic_outcomes = [o for o in outcomes if o.deriver_name == "semantic_topic_cluster"]
    assert len(topic_outcomes) == 1
    assert topic_outcomes[0].status == "succeeded"

    with database_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT cluster_index, cluster_label, distance_to_centroid "
                "FROM semantic.topic_cluster_assignment"
            )
        ).all()

    # 65 synthetic units + the one real seed-event text unit _seed_one_real_epoch's own real
    # pipeline created -- semantic_topic_cluster is GLOBAL over the *whole* embedding population,
    # not just this test's synthetic subset, so that real row is legitimately part of it too.
    assert len(rows) == 66
    real_clusters = {r.cluster_index for r in rows if r.cluster_index != -1}
    assert len(real_clusters) == 2, f"expected exactly 2 real clusters, got {real_clusters}"
    noise_rows = [r for r in rows if r.cluster_index == -1]
    # The 5 scattered synthetic outliers plus the one real, topically-unrelated seed unit.
    assert len(noise_rows) >= 5, "the scattered outliers should land in the noise group"
    # Exit test 7: noise rows are honestly NULL, never forced to a fabricated distance.
    assert all(r.distance_to_centroid is None for r in noise_rows)
    assert all(r.distance_to_centroid is not None for r in rows if r.cluster_index != -1)


def test_rerun_with_unchanged_embeddings_is_a_noop(database_engine: Engine) -> None:
    epoch_id = _seed_one_real_epoch(database_engine)
    _seed_two_blobs_and_noise(database_engine, epoch_id)

    first = derive_run(database_engine, include_global=True)
    first_outcome = next(o for o in first if o.deriver_name == "semantic_topic_cluster")
    assert first_outcome.status == "succeeded"

    # A clean partition is never reclaimed at all -- real no-op behavior here means "nothing left
    # to do," not a repeated compute() call. To actually exercise the fingerprint-skip path (rather
    # than just "there was nothing dirty"), explicitly re-dirty the GLOBAL partition (as if some
    # unrelated upstream event had touched it) with the underlying embeddings genuinely unchanged,
    # and confirm compute() itself recognizes that and skips.
    with database_engine.begin() as connection:
        mark_dirty(connection, "semantic_topic_cluster", ["GLOBAL"])

    second = derive_run(database_engine, include_global=True)
    topic_second = [o for o in second if o.deriver_name == "semantic_topic_cluster"]
    assert len(topic_second) == 1
    assert topic_second[0].status == "skipped_unchanged"


def test_clear_partition_only_deletes_the_active_versions_rows(database_engine: Engine) -> None:
    epoch_id = _seed_one_real_epoch(database_engine)
    _seed_two_blobs_and_noise(database_engine, epoch_id)
    derive_run(database_engine, include_global=True)

    with database_engine.begin() as connection:
        active_id = active_version_id(connection, "semantic_topic_cluster")
        active_count_before = connection.execute(
            text(
                "SELECT count(*) FROM semantic.topic_cluster_assignment WHERE deriver_version_id = :vid"
            ),
            {"vid": active_id},
        ).scalar_one()
        assert active_count_before == 66  # 65 synthetic + 1 real seed unit, see the test above

        # A shadow version -- a different version_hash for the same deriver, never promoted --
        # coexisting per versioning.py's own shadow/active precedent, with one fake row of its own.
        shadow_id = connection.execute(
            text(
                """
                INSERT INTO derive.deriver_version
                    (deriver_name, code_version, params, model_ref, version_hash, status)
                VALUES ('semantic_topic_cluster', '999', '{}'::jsonb, NULL,
                        decode(:hash, 'hex'), 'shadow')
                RETURNING id
                """
            ),
            {"hash": hashlib.sha256(b"shadow-test").hexdigest()},
        ).scalar_one()
        any_text_unit_id = connection.execute(
            text("SELECT id FROM semantic.text_unit LIMIT 1")
        ).scalar_one()
        connection.execute(
            text(
                """
                INSERT INTO semantic.topic_cluster_assignment
                    (text_unit_id, deriver_version_id, cluster_index, cluster_label,
                     distance_to_centroid, computed_epoch_id)
                VALUES (:tid, :vid, 0, 'shadow-label', 0.0, :epoch_id)
                """
            ),
            {"tid": any_text_unit_id, "vid": shadow_id, "epoch_id": epoch_id},
        )

        _clear_topic_cluster_assignment(connection, "GLOBAL")

        active_count_after = connection.execute(
            text(
                "SELECT count(*) FROM semantic.topic_cluster_assignment WHERE deriver_version_id = :vid"
            ),
            {"vid": active_id},
        ).scalar_one()
        shadow_count_after = connection.execute(
            text(
                "SELECT count(*) FROM semantic.topic_cluster_assignment WHERE deriver_version_id = :vid"
            ),
            {"vid": shadow_id},
        ).scalar_one()

    assert active_count_after == 0
    assert shadow_count_after == 1


def test_l2_normalized_euclidean_matches_cosine_hdbscan_membership() -> None:
    rng = np.random.default_rng(0)
    a = rng.normal(loc=[1, 0, 0], scale=0.05, size=(6, 3))
    b = rng.normal(loc=[0, 1, 0], scale=0.05, size=(6, 3))
    outliers = rng.normal(loc=[0, 0, 5], scale=0.5, size=(3, 3))
    vectors = np.vstack([a, b, outliers])

    from sklearn.cluster import HDBSCAN

    cosine_labels = HDBSCAN(
        metric="cosine", min_cluster_size=2, min_samples=1, copy=False
    ).fit_predict(vectors)

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    normalized = vectors / norms
    euclidean_on_normalized_labels = _cluster(normalized, min_cluster_size=2, min_samples=1)

    assert list(euclidean_on_normalized_labels) == list(cosine_labels)


def test_label_cluster_surfaces_real_shared_vocabulary_and_does_not_fabricate_one() -> None:
    shared_texts = [
        "The lease renewal for the apartment is due next month, landlord wants an answer",
        "Our landlord asked about the lease again, the apartment lease needs signing soon",
        "Lease terms for the apartment were finalized, landlord confirmed the move-in date",
    ]
    no_shared_vocab_texts = [
        "Completely unrelated content about something else entirely with no overlap",
        "A second sentence sharing literally zero real words with the first one here",
    ]
    all_texts = shared_texts + no_shared_vocab_texts
    vectorizer = TfidfVectorizer(stop_words="english")
    matrix = vectorizer.fit_transform(all_texts)
    feature_names = vectorizer.get_feature_names_out()

    shared_label = _label_cluster(np.array([0, 1, 2]), matrix, feature_names)
    assert "lease" in shared_label or "apartment" in shared_label or "landlord" in shared_label

    # Two units sharing no real vocabulary beyond stopwords -- TF-IDF for every remaining term is
    # near-unique to one document, so nothing should look like a genuinely *shared* topic term.
    disjoint_label = _label_cluster(np.array([3, 4]), matrix, feature_names)
    assert disjoint_label != shared_label
