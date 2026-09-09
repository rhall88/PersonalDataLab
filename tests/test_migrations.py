from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text

from personal_data_lab.migrations import migrate_database

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
    with admin.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
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

    assert migrate_database(pr2_engine) == (3, 4)
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
            text("SELECT COUNT(*) FROM event_observations")
        ).scalar_one()
    assert list(versions) == [2, 3, 4]
    assert tuple(event) == ("message", "user", 32, "canonical-v1")
    assert observations == 1
