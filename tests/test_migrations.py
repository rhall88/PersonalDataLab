from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from personal_data_lab.fingerprints import event_fingerprint
from personal_data_lab.migrations import (
    _backfill_fingerprints,
    _backfill_legacy_observations,
    _ensure_migration_table,
    _read_migration,
    current_schema_version,
    migrate_database,
)
from personal_data_lab.models import NormalizedEvent

pytestmark = pytest.mark.integration

PR2_SCHEMA = """
CREATE TABLE source_files (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    filename TEXT NOT NULL,
    original_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    exported_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    parser_version TEXT NOT NULL,
    UNIQUE (source, sha256)
);
CREATE TABLE conversations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    source_conversation_id TEXT NOT NULL,
    title TEXT,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (source, source_conversation_id)
);
CREATE TABLE source_file_conversations (
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    PRIMARY KEY (source_file_id, conversation_id)
);
CREATE TABLE people (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    display_name TEXT NOT NULL
);
CREATE TABLE events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at TIMESTAMPTZ,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_person_id BIGINT REFERENCES people(id),
    conversation_id BIGINT REFERENCES conversations(id),
    text TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_record_id TEXT NOT NULL,
    source_node_id TEXT,
    parent_source_node_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source, source_record_id)
);
CREATE TABLE source_file_events (
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    PRIMARY KEY (source_file_id, event_id)
);
CREATE TABLE attachments (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    mime_type TEXT,
    path TEXT NOT NULL,
    sha256 TEXT,
    file_size BIGINT,
    width INTEGER,
    height INTEGER,
    duration DOUBLE PRECISION,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
"""


@pytest.fixture
def pr2_engine() -> Iterator[Engine]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    admin = create_engine(database_url)
    schema = "migration_pr2"
    # Migration 010 creates derive/declare/policy/measure as top-level schemas, not inside
    # `schema`, so they need their own cleanup on both sides of the test -- CASCADE handles
    # cross-schema FKs between them (e.g. measure.* referencing derive.evidence_epoch). Migration
    # 012 adds `identity` the same way (identity.resolution_version_seq, identity.assertion,
    # identity.not_same_as) -- without it here, the sequence survives from one test to the next
    # in the shared TEST_DATABASE_URL database and the next migrate_database(pr2_engine) call
    # fails with DuplicateTable on identity.resolution_version_seq.
    derived_schemas = "derive, declare, policy, measure, identity"
    with admin.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        connection.execute(text(f"DROP SCHEMA IF EXISTS {derived_schemas} CASCADE"))
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_engine(
        database_url,
        connect_args={"options": f"-csearch_path={schema},public"},
    )
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(PR2_SCHEMA)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            connection.execute(text(f"DROP SCHEMA IF EXISTS {derived_schemas} CASCADE"))
            # This fixture's own setup above already dropped these shared, top-level schemas
            # once, so PR2 -> migration 10 can be tested from a clean slate. Restoring them here
            # (rather than leaving personaldata_test missing them for every test file that
            # happens to run after this one) keeps the database in the same freshly-migrated
            # state a plain bootstrap leaves it in.
            connection.exec_driver_sql(_read_migration(10))
            connection.exec_driver_sql(_read_migration(11))
            connection.exec_driver_sql(_read_migration(12))
            connection.exec_driver_sql(_read_migration(13))
        admin.dispose()


def test_pr2_database_migrates_and_backfills_provenance(pr2_engine: Engine) -> None:
    with pr2_engine.begin() as connection:
        source_file_id = connection.execute(
            text(
                """
                INSERT INTO source_files
                    (source, filename, original_path, sha256, size_bytes, parser_version)
                VALUES ('openai', 'conversations.json', 'synthetic://legacy', :sha256, 1,
                        'openai-v1')
                RETURNING id
                """
            ),
            {"sha256": "a" * 64},
        ).scalar_one()
        conversation_id = connection.execute(
            text(
                """
                INSERT INTO conversations
                    (source, source_conversation_id, title, metadata)
                VALUES ('openai', 'conversation-1', 'Legacy', '{}'::jsonb)
                RETURNING id
                """
            )
        ).scalar_one()
        event_id = connection.execute(
            text(
                """
                INSERT INTO events
                    (source, event_type, conversation_id, text, metadata, source_record_id)
                VALUES ('openai', 'conversation_message', :conversation_id, 'hello',
                        '{"author_role":"user"}'::jsonb, 'message-1')
                RETURNING id
                """
            ),
            {"conversation_id": conversation_id},
        ).scalar_one()
        connection.execute(
            text(
                """
                INSERT INTO source_file_conversations VALUES (:source_file_id, :record_id)
                """
            ),
            {"source_file_id": source_file_id, "record_id": conversation_id},
        )
        connection.execute(
            text("INSERT INTO source_file_events VALUES (:source_file_id, :record_id)"),
            {"source_file_id": source_file_id, "record_id": event_id},
        )

    assert migrate_database(pr2_engine) == (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13)
    assert migrate_database(pr2_engine) == ()

    with pr2_engine.connect() as connection:
        versions = connection.execute(
            text("SELECT version FROM schema_migrations ORDER BY version")
        ).scalars().all()
        event = connection.execute(
            text(
                """
                SELECT source_record_namespace, actor_identity,
                       octet_length(content_fingerprint), fingerprint_version
                FROM events
                """
            )
        ).one()
        observations = connection.execute(
            text(
                """
                SELECT COUNT(*), COUNT(event_version_id)
                FROM event_observations
                """
            )
        ).one()
        event_versions = connection.execute(
            text(
                """
                SELECT payload_fidelity, normalized_payload ->> 'text'
                FROM event_versions
                """
            )
        ).one()
    assert list(versions) == [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
    assert tuple(event) == ("message", "user", 32, "canonical-v1")
    assert tuple(observations) == (1, 1)
    assert tuple(event_versions) == ("full", "hello")

    with pr2_engine.connect() as connection:
        target_tables = (
            "import_runs", "conversations", "events", "attachments", "conversation_versions",
            "event_versions", "attachment_versions", "conversation_observations",
            "event_observations", "attachment_observations",
            "records", "record_versions", "record_observations",
            "assets", "asset_versions", "asset_observations",
            "relationships", "relationship_versions", "relationship_observations",
        )
        columns: dict[str, set[tuple[str, str, str]]] = {}
        for schema in ("public", "migration_pr2"):
            rows = connection.execute(
                text(
                    """
                    SELECT table_name, column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = :schema AND table_name = ANY(:tables)
                    """
                ),
                {"schema": schema, "tables": list(target_tables)},
            ).all()
            columns[schema] = {
                (str(table), str(column), f"{data_type}:{nullable}")
                for table, column, data_type, nullable in rows
            }
    assert columns["migration_pr2"] == columns["public"]


def test_v5_preserves_unreconstructable_observation_as_fingerprint_only(
    pr2_engine: Engine,
) -> None:
    with pr2_engine.begin() as connection:
        source_file_id = connection.execute(
            text(
                """
                INSERT INTO source_files
                    (source, filename, original_path, sha256, size_bytes, parser_version)
                VALUES ('openai', 'conversations.json', 'synthetic://legacy', :sha256, 1,
                        'openai-v1')
                RETURNING id
                """
            ),
            {"sha256": "b" * 64},
        ).scalar_one()
        conversation_id = connection.execute(
            text(
                """
                INSERT INTO conversations (source, source_conversation_id, title, metadata)
                VALUES ('openai', 'conversation-1', 'Legacy', '{}'::jsonb)
                RETURNING id
                """
            )
        ).scalar_one()
        event_id = connection.execute(
            text(
                """
                INSERT INTO events
                    (source, event_type, conversation_id, text, metadata, source_record_id)
                VALUES ('openai', 'conversation_message', :conversation_id, 'old',
                        '{"author_role":"user"}'::jsonb, 'message-1')
                RETURNING id
                """
            ),
            {"conversation_id": conversation_id},
        ).scalar_one()
        connection.execute(
            text("INSERT INTO source_file_conversations VALUES (:file_id, :record_id)"),
            {"file_id": source_file_id, "record_id": conversation_id},
        )
        connection.execute(
            text("INSERT INTO source_file_events VALUES (:file_id, :record_id)"),
            {"file_id": source_file_id, "record_id": event_id},
        )
        _ensure_migration_table(connection)
        connection.exec_driver_sql(_read_migration(3))
        _backfill_fingerprints(connection)
        _backfill_legacy_observations(connection)
        connection.execute(text("INSERT INTO schema_migrations (version) VALUES (3)"))
        connection.exec_driver_sql(_read_migration(4))
        connection.execute(text("INSERT INTO schema_migrations (version) VALUES (4)"))
        old_fingerprint = bytes(
            connection.execute(
                text("SELECT content_fingerprint FROM events WHERE id = :event_id"),
                {"event_id": event_id},
            ).scalar_one()
        )
        edited = NormalizedEvent(
            occurred_at=None,
            source="openai",
            event_type="conversation_message",
            source_record_namespace="message",
            actor_identity="user",
            text="new",
            metadata={"author_role": "user"},
            source_record_id="message-1",
        )
        new_fingerprint = event_fingerprint(
            edited,
            conversation_namespace="chat",
            conversation_source_id="conversation-1",
        )
        connection.execute(
            text("UPDATE events SET text = 'new', content_fingerprint = :fingerprint"),
            {"fingerprint": new_fingerprint},
        )

    assert migrate_database(pr2_engine) == (5, 6, 7, 8, 9, 10, 11, 12, 13)

    with pr2_engine.connect() as connection:
        versions = connection.execute(
            text(
                """
                SELECT content_fingerprint, payload_fidelity, normalized_payload ->> 'text'
                FROM event_versions ORDER BY payload_fidelity
                """
            )
        ).all()
        observed = connection.execute(
            text(
                """
                SELECT ev.content_fingerprint, ev.payload_fidelity
                FROM event_observations eo
                JOIN event_versions ev ON ev.id = eo.event_version_id
                """
            )
        ).one()
    assert {(bytes(fp), fidelity, value) for fp, fidelity, value in versions} == {
        (new_fingerprint, "full", "new"),
        (old_fingerprint, "fingerprint_only", None),
    }
    assert (bytes(observed[0]), observed[1]) == (old_fingerprint, "fingerprint_only")


@pytest.fixture
def fresh_database_engine() -> Iterator[Engine]:
    # A schema-scoped fixture isn't enough here: to_regclass()/unqualified
    # lookups fall back through the rest of the search_path, so if the real
    # TEST_DATABASE_URL database already has a migrated public.schema_migrations
    # (it does, in normal use), an isolated *schema* would still "see" it.
    # A genuinely separate database avoids that ambiguity entirely.
    admin_url = os.getenv("TEST_DATABASE_URL")
    if not admin_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    db_name = "pdl_doctor_fresh_test"
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text(f"DROP DATABASE IF EXISTS {db_name}"))
        connection.execute(text(f"CREATE DATABASE {db_name}"))
    fresh_url = make_url(admin_url).set(database=db_name)
    engine = create_engine(fresh_url)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {db_name}"))
        admin_engine.dispose()


def test_current_schema_version_returns_none_for_unmigrated_database(
    fresh_database_engine: Engine,
) -> None:
    assert current_schema_version(fresh_database_engine) is None


def test_current_schema_version_reads_max_applied_version(
    fresh_database_engine: Engine,
) -> None:
    with fresh_database_engine.begin() as connection:
        connection.exec_driver_sql(PR2_SCHEMA)

    # PR2_SCHEMA has canonical tables but no schema_migrations table yet.
    assert current_schema_version(fresh_database_engine) is None

    migrate_database(fresh_database_engine)

    assert current_schema_version(fresh_database_engine) == 13


def test_migration_010_creates_derive_schemas_and_seeds_local_only_policy(
    pr2_engine: Engine,
) -> None:
    migrate_database(pr2_engine)

    with pr2_engine.connect() as connection:
        schemas = set(
            connection.execute(
                text(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name IN ('derive', 'declare', 'policy', 'measure')"
                )
            ).scalars()
        )
        assert schemas == {"derive", "declare", "policy", "measure"}

        # Every source starts local_only (architecture inv. 29): nothing leaves the machine
        # until an egress_override backed by a declaration says otherwise.
        source_egress = dict(
            connection.execute(
                text("SELECT source, egress FROM policy.source_policy")
            ).all()
        )
        assert source_egress == {
            "anthropic": "local_only",
            "openai": "local_only",
            "imessages": "local_only",
            "apple": "local_only",
            "google": "local_only",
            "facebook": "local_only",
        }

        sensitivity = dict(
            connection.execute(
                text("SELECT source, sensitivity FROM policy.source_policy")
            ).all()
        )
        assert sensitivity["imessages"] == ["intimate"]
        assert sensitivity["facebook"] == ["intimate"]
        assert sensitivity["openai"] == ["private"]

        # Postgres orders ENUM values by declaration, so GREATEST() over egress_level computes
        # the lattice's max-restriction directly -- this is the mechanism inv. 24 relies on.
        ordering = connection.execute(
            text(
                "SELECT GREATEST('external_model_allowed'::policy.egress_level, "
                "'redactable'::policy.egress_level, 'local_only'::policy.egress_level)"
            )
        ).scalar_one()
        assert ordering == "local_only"

        # derive.deriver is intentionally empty after migration -- deriver definitions are code
        # (registry.py), not schema, so no migration seeds them.
        deriver_count = connection.execute(
            text("SELECT COUNT(*) FROM derive.deriver")
        ).scalar_one()
        assert deriver_count == 0

        cursor = connection.execute(
            text("SELECT name, last_import_run_id FROM derive.change_cursor")
        ).one()
        assert tuple(cursor) == ("default", 0)


def test_migration_010_touches_no_canonical_table(pr2_engine: Engine) -> None:
    # Snapshot the columns of the PR2-baseline canonical tables (the ones migration 10 must
    # never touch) before running migrations 3-9, so a diff against the same tables after
    # migration 10 isolates exactly what that one migration changed.
    canonical_tables = (
        "source_files", "conversations", "people", "events",
        "source_file_conversations", "source_file_events", "attachments",
    )
    with pr2_engine.begin() as connection:
        _ensure_migration_table(connection)
        before = connection.execute(
            text(
                "SELECT table_name, column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'migration_pr2' AND table_name = ANY(:tables) "
                "ORDER BY table_name, column_name"
            ),
            {"tables": list(canonical_tables)},
        ).all()

    # Run only migrations 3-9 first (skip 10+) so `before` reflects the same baseline the
    # existing PR2 test already validates, then apply migrations 10+ (via migrate_database,
    # which runs everything through LATEST_SCHEMA_VERSION -- currently 12 -- so this test
    # automatically covers each new migration as LATEST_SCHEMA_VERSION is bumped) and diff.
    with pr2_engine.begin() as connection:
        for version in range(3, 10):
            connection.exec_driver_sql(_read_migration(version))
            if version == 3:
                _backfill_fingerprints(connection)
                _backfill_legacy_observations(connection)
            connection.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                {"version": version},
            )
        mid = connection.execute(
            text(
                "SELECT table_name, column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'migration_pr2' AND table_name = ANY(:tables) "
                "ORDER BY table_name, column_name"
            ),
            {"tables": list(canonical_tables)},
        ).all()

    migrate_database(pr2_engine)  # applies migrations 10 through LATEST_SCHEMA_VERSION

    with pr2_engine.connect() as connection:
        after = connection.execute(
            text(
                "SELECT table_name, column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'migration_pr2' AND table_name = ANY(:tables) "
                "ORDER BY table_name, column_name"
            ),
            {"tables": list(canonical_tables)},
        ).all()

    assert mid == after, "migrations 10+ must not alter any canonical table"
    assert before != mid, "sanity check: migrations 3-9 should have changed something"
