CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_files (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    filename TEXT NOT NULL,
    original_path TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    exported_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    parser_version TEXT NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source, sha256)
);

CREATE TABLE IF NOT EXISTS source_file_locations (
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    original_path TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_file_id, original_path)
);

CREATE TABLE IF NOT EXISTS import_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    export_id TEXT,
    run_key BYTEA NOT NULL CHECK (octet_length(run_key) = 32),
    snapshot_kind TEXT NOT NULL CHECK (snapshot_kind IN ('unknown', 'partial', 'complete')),
    snapshot_scope TEXT NOT NULL,
    exported_at TIMESTAMPTZ,
    parser_version TEXT NOT NULL,
    fingerprint_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('staging', 'finalizing', 'partial', 'completed', 'failed', 'conflicted')
    ),
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    stats JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_summary TEXT,
    UNIQUE (run_key)
);

CREATE TABLE IF NOT EXISTS import_run_source_files (
    import_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE RESTRICT,
    category TEXT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (import_run_id, source_file_id)
);

CREATE TABLE IF NOT EXISTS people (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS identities (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id BIGINT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    identity_type TEXT NOT NULL,
    identity_value TEXT NOT NULL,
    source TEXT NOT NULL,
    UNIQUE (identity_type, identity_value, source)
);

CREATE TABLE IF NOT EXISTS conversations (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    source_conversation_id TEXT NOT NULL,
    source_record_namespace TEXT NOT NULL,
    title TEXT,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_fingerprint BYTEA NOT NULL CHECK (octet_length(content_fingerprint) = 32),
    fingerprint_version TEXT NOT NULL,
    provider_updated_at TIMESTAMPTZ,
    authority_at TIMESTAMPTZ,
    authority_kind TEXT NOT NULL CHECK (
        authority_kind IN ('provider_revision', 'provider_updated_at', 'exported_at', 'unknown')
    ),
    canonical_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    canonical_revision BIGINT NOT NULL DEFAULT 1 CHECK (canonical_revision > 0),
    canonical_updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK (
        lifecycle_state IN ('active', 'provider_deleted')
    ),
    tombstoned_at TIMESTAMPTZ,
    tombstone_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    UNIQUE (source, source_record_namespace, source_conversation_id)
);

CREATE TABLE IF NOT EXISTS source_file_conversations (
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    PRIMARY KEY (source_file_id, conversation_id)
);

CREATE TABLE IF NOT EXISTS events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at TIMESTAMPTZ,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source_record_namespace TEXT NOT NULL,
    actor_person_id BIGINT REFERENCES people(id) ON DELETE SET NULL,
    actor_identity TEXT,
    conversation_id BIGINT REFERENCES conversations(id) ON DELETE SET NULL,
    text TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_record_id TEXT NOT NULL,
    source_node_id TEXT,
    parent_source_node_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    content_fingerprint BYTEA NOT NULL CHECK (octet_length(content_fingerprint) = 32),
    fingerprint_version TEXT NOT NULL,
    provider_updated_at TIMESTAMPTZ,
    authority_at TIMESTAMPTZ,
    authority_kind TEXT NOT NULL CHECK (
        authority_kind IN ('provider_revision', 'provider_updated_at', 'exported_at', 'unknown')
    ),
    canonical_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    canonical_revision BIGINT NOT NULL DEFAULT 1 CHECK (canonical_revision > 0),
    canonical_updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK (
        lifecycle_state IN ('active', 'provider_deleted')
    ),
    tombstoned_at TIMESTAMPTZ,
    tombstone_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    UNIQUE (source, source_record_namespace, source_record_id)
);

CREATE TABLE IF NOT EXISTS source_file_events (
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    PRIMARY KEY (source_file_id, event_id)
);

CREATE TABLE IF NOT EXISTS attachments (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_record_namespace TEXT NOT NULL,
    source_attachment_id TEXT NOT NULL,
    mime_type TEXT,
    path TEXT NOT NULL,
    sha256 TEXT CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
    file_size BIGINT CHECK (file_size IS NULL OR file_size >= 0),
    width INTEGER CHECK (width IS NULL OR width >= 0),
    height INTEGER CHECK (height IS NULL OR height >= 0),
    duration DOUBLE PRECISION CHECK (duration IS NULL OR duration >= 0),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_fingerprint BYTEA NOT NULL CHECK (octet_length(content_fingerprint) = 32),
    fingerprint_version TEXT NOT NULL,
    provider_updated_at TIMESTAMPTZ,
    authority_at TIMESTAMPTZ,
    authority_kind TEXT NOT NULL CHECK (
        authority_kind IN ('provider_revision', 'provider_updated_at', 'exported_at', 'unknown')
    ),
    canonical_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    canonical_revision BIGINT NOT NULL DEFAULT 1 CHECK (canonical_revision > 0),
    canonical_updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK (
        lifecycle_state IN ('active', 'provider_deleted')
    ),
    tombstoned_at TIMESTAMPTZ,
    tombstone_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    UNIQUE (source, source_record_namespace, source_attachment_id)
);

CREATE TABLE IF NOT EXISTS conversation_observations (
    import_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE RESTRICT,
    conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    observed_content_fingerprint BYTEA NOT NULL CHECK (
        octet_length(observed_content_fingerprint) = 32
    ),
    raw_record_fingerprint BYTEA CHECK (
        raw_record_fingerprint IS NULL OR octet_length(raw_record_fingerprint) = 32
    ),
    provider_updated_at TIMESTAMPTZ,
    outcome TEXT NOT NULL CHECK (
        outcome IN ('inserted', 'updated', 'unchanged', 'stale', 'tombstoned')
    ),
    PRIMARY KEY (import_run_id, source_file_id, conversation_id)
);

CREATE TABLE IF NOT EXISTS event_observations (
    import_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE RESTRICT,
    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    observed_content_fingerprint BYTEA NOT NULL CHECK (
        octet_length(observed_content_fingerprint) = 32
    ),
    raw_record_fingerprint BYTEA CHECK (
        raw_record_fingerprint IS NULL OR octet_length(raw_record_fingerprint) = 32
    ),
    provider_updated_at TIMESTAMPTZ,
    outcome TEXT NOT NULL CHECK (
        outcome IN ('inserted', 'updated', 'unchanged', 'stale', 'tombstoned')
    ),
    PRIMARY KEY (import_run_id, source_file_id, event_id)
);

CREATE TABLE IF NOT EXISTS attachment_observations (
    import_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE RESTRICT,
    attachment_id BIGINT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    observed_content_fingerprint BYTEA NOT NULL CHECK (
        octet_length(observed_content_fingerprint) = 32
    ),
    raw_record_fingerprint BYTEA CHECK (
        raw_record_fingerprint IS NULL OR octet_length(raw_record_fingerprint) = 32
    ),
    provider_updated_at TIMESTAMPTZ,
    outcome TEXT NOT NULL CHECK (
        outcome IN ('inserted', 'updated', 'unchanged', 'stale', 'tombstoned')
    ),
    PRIMARY KEY (import_run_id, source_file_id, attachment_id)
);

CREATE INDEX IF NOT EXISTS events_occurred_at_idx ON events (occurred_at);
CREATE INDEX IF NOT EXISTS events_source_type_idx ON events (source, event_type);
CREATE INDEX IF NOT EXISTS events_conversation_idx ON events (conversation_id, occurred_at);
CREATE INDEX IF NOT EXISTS events_source_node_idx ON events (source_node_id);
CREATE INDEX IF NOT EXISTS source_file_conversations_conversation_idx
    ON source_file_conversations (conversation_id);
CREATE INDEX IF NOT EXISTS source_file_events_event_idx ON source_file_events (event_id);
CREATE INDEX IF NOT EXISTS conversation_observations_conversation_idx
    ON conversation_observations (conversation_id, import_run_id);
CREATE INDEX IF NOT EXISTS event_observations_event_idx
    ON event_observations (event_id, import_run_id);
CREATE INDEX IF NOT EXISTS attachment_observations_attachment_idx
    ON attachment_observations (attachment_id, import_run_id);

INSERT INTO schema_migrations (version) VALUES (2), (3), (4) ON CONFLICT DO NOTHING;
