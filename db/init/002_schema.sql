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
    policy_version TEXT NOT NULL,
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
    first_observed_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE RESTRICT,
    last_observed_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE RESTRICT,
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
    first_observed_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE RESTRICT,
    last_observed_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE RESTRICT,
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
    first_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    last_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    canonical_revision BIGINT NOT NULL DEFAULT 1 CHECK (canonical_revision > 0),
    canonical_updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK (
        lifecycle_state IN ('active', 'provider_deleted')
    ),
    tombstoned_at TIMESTAMPTZ,
    tombstone_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    UNIQUE (source, source_record_namespace, source_attachment_id)
);

CREATE TABLE IF NOT EXISTS conversation_versions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    content_fingerprint BYTEA NOT NULL CHECK (octet_length(content_fingerprint) = 32),
    fingerprint_version TEXT NOT NULL,
    normalized_payload JSONB,
    payload_fidelity TEXT NOT NULL CHECK (payload_fidelity IN ('full', 'fingerprint_only')),
    first_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    last_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    provider_updated_at TIMESTAMPTZ,
    authority_at TIMESTAMPTZ,
    authority_kind TEXT NOT NULL CHECK (
        authority_kind IN ('provider_revision', 'provider_updated_at', 'exported_at', 'unknown')
    ),
    lifecycle_state TEXT CHECK (lifecycle_state IN ('active', 'provider_deleted')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (conversation_id, fingerprint_version, content_fingerprint),
    CHECK (payload_fidelity = 'fingerprint_only' OR normalized_payload IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS event_versions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    content_fingerprint BYTEA NOT NULL CHECK (octet_length(content_fingerprint) = 32),
    fingerprint_version TEXT NOT NULL,
    normalized_payload JSONB,
    payload_fidelity TEXT NOT NULL CHECK (payload_fidelity IN ('full', 'fingerprint_only')),
    first_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    last_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    provider_updated_at TIMESTAMPTZ,
    authority_at TIMESTAMPTZ,
    authority_kind TEXT NOT NULL CHECK (
        authority_kind IN ('provider_revision', 'provider_updated_at', 'exported_at', 'unknown')
    ),
    lifecycle_state TEXT CHECK (lifecycle_state IN ('active', 'provider_deleted')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (event_id, fingerprint_version, content_fingerprint),
    CHECK (payload_fidelity = 'fingerprint_only' OR normalized_payload IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS attachment_versions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    attachment_id BIGINT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    content_fingerprint BYTEA NOT NULL CHECK (octet_length(content_fingerprint) = 32),
    fingerprint_version TEXT NOT NULL,
    normalized_payload JSONB,
    payload_fidelity TEXT NOT NULL CHECK (payload_fidelity IN ('full', 'fingerprint_only')),
    first_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    last_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    provider_updated_at TIMESTAMPTZ,
    authority_at TIMESTAMPTZ,
    authority_kind TEXT NOT NULL CHECK (
        authority_kind IN ('provider_revision', 'provider_updated_at', 'exported_at', 'unknown')
    ),
    lifecycle_state TEXT CHECK (lifecycle_state IN ('active', 'provider_deleted')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (attachment_id, fingerprint_version, content_fingerprint),
    CHECK (payload_fidelity = 'fingerprint_only' OR normalized_payload IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS conversation_observations (
    import_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE RESTRICT,
    conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    conversation_version_id BIGINT NOT NULL REFERENCES conversation_versions(id) ON DELETE RESTRICT,
    raw_record_fingerprint BYTEA CHECK (
        raw_record_fingerprint IS NULL OR octet_length(raw_record_fingerprint) = 32
    ),
    outcome TEXT NOT NULL CHECK (
        outcome IN ('inserted', 'updated', 'unchanged', 'stale', 'conflicted', 'tombstoned')
    ),
    PRIMARY KEY (import_run_id, source_file_id, conversation_id)
);

CREATE TABLE IF NOT EXISTS event_observations (
    import_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE RESTRICT,
    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    event_version_id BIGINT NOT NULL REFERENCES event_versions(id) ON DELETE RESTRICT,
    raw_record_fingerprint BYTEA CHECK (
        raw_record_fingerprint IS NULL OR octet_length(raw_record_fingerprint) = 32
    ),
    outcome TEXT NOT NULL CHECK (
        outcome IN ('inserted', 'updated', 'unchanged', 'stale', 'conflicted', 'tombstoned')
    ),
    PRIMARY KEY (import_run_id, source_file_id, event_id)
);

CREATE TABLE IF NOT EXISTS attachment_observations (
    import_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE RESTRICT,
    attachment_id BIGINT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    attachment_version_id BIGINT NOT NULL REFERENCES attachment_versions(id) ON DELETE RESTRICT,
    raw_record_fingerprint BYTEA CHECK (
        raw_record_fingerprint IS NULL OR octet_length(raw_record_fingerprint) = 32
    ),
    outcome TEXT NOT NULL CHECK (
        outcome IN ('inserted', 'updated', 'unchanged', 'stale', 'conflicted', 'tombstoned')
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
CREATE INDEX IF NOT EXISTS conversation_versions_history_idx
    ON conversation_versions (conversation_id, created_at);
CREATE INDEX IF NOT EXISTS event_versions_history_idx
    ON event_versions (event_id, created_at);
CREATE INDEX IF NOT EXISTS attachment_versions_history_idx
    ON attachment_versions (attachment_id, created_at);

CREATE TABLE records (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    record_type TEXT NOT NULL,
    source_record_namespace TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    title TEXT,
    text TEXT,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ,
    occurred_at TIMESTAMPTZ,
    actor_identity TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_fingerprint BYTEA NOT NULL CHECK (octet_length(content_fingerprint) = 32),
    fingerprint_version TEXT NOT NULL,
    provider_updated_at TIMESTAMPTZ,
    authority_at TIMESTAMPTZ,
    authority_kind TEXT NOT NULL CHECK (
        authority_kind IN ('provider_revision', 'provider_updated_at', 'exported_at', 'unknown')
    ),
    canonical_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    first_observed_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE RESTRICT,
    last_observed_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE RESTRICT,
    canonical_revision BIGINT NOT NULL DEFAULT 1 CHECK (canonical_revision > 0),
    canonical_updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK (
        lifecycle_state IN ('active', 'provider_deleted')
    ),
    tombstoned_at TIMESTAMPTZ,
    tombstone_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    UNIQUE (source, source_record_namespace, source_record_id)
);

CREATE TABLE source_file_records (
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    record_id BIGINT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    PRIMARY KEY (source_file_id, record_id)
);

CREATE TABLE record_versions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    record_id BIGINT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    content_fingerprint BYTEA NOT NULL CHECK (octet_length(content_fingerprint) = 32),
    fingerprint_version TEXT NOT NULL,
    normalized_payload JSONB,
    payload_fidelity TEXT NOT NULL CHECK (payload_fidelity IN ('full', 'fingerprint_only')),
    first_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    last_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    provider_updated_at TIMESTAMPTZ,
    authority_at TIMESTAMPTZ,
    authority_kind TEXT NOT NULL CHECK (
        authority_kind IN ('provider_revision', 'provider_updated_at', 'exported_at', 'unknown')
    ),
    lifecycle_state TEXT CHECK (lifecycle_state IN ('active', 'provider_deleted')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (record_id, fingerprint_version, content_fingerprint),
    CHECK (payload_fidelity = 'fingerprint_only' OR normalized_payload IS NOT NULL)
);

CREATE TABLE record_observations (
    import_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE RESTRICT,
    record_id BIGINT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    record_version_id BIGINT NOT NULL REFERENCES record_versions(id) ON DELETE RESTRICT,
    raw_record_fingerprint BYTEA CHECK (
        raw_record_fingerprint IS NULL OR octet_length(raw_record_fingerprint) = 32
    ),
    outcome TEXT NOT NULL CHECK (
        outcome IN ('inserted', 'updated', 'unchanged', 'stale', 'conflicted', 'tombstoned')
    ),
    PRIMARY KEY (import_run_id, source_file_id, record_id)
);

CREATE TABLE assets (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    asset_namespace TEXT NOT NULL,
    source_asset_id TEXT NOT NULL,
    sha256 TEXT CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
    mime_type TEXT,
    filename TEXT NOT NULL,
    original_path TEXT NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    created_at TIMESTAMPTZ,
    captured_at TIMESTAMPTZ,
    modified_at TIMESTAMPTZ,
    width INTEGER CHECK (width IS NULL OR width >= 0),
    height INTEGER CHECK (height IS NULL OR height >= 0),
    duration_ms BIGINT CHECK (duration_ms IS NULL OR duration_ms >= 0),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_fingerprint BYTEA NOT NULL CHECK (octet_length(content_fingerprint) = 32),
    fingerprint_version TEXT NOT NULL,
    provider_updated_at TIMESTAMPTZ,
    authority_at TIMESTAMPTZ,
    authority_kind TEXT NOT NULL CHECK (
        authority_kind IN ('provider_revision', 'provider_updated_at', 'exported_at', 'unknown')
    ),
    canonical_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    first_observed_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE RESTRICT,
    last_observed_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE RESTRICT,
    canonical_revision BIGINT NOT NULL DEFAULT 1 CHECK (canonical_revision > 0),
    canonical_updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK (
        lifecycle_state IN ('active', 'provider_deleted')
    ),
    tombstoned_at TIMESTAMPTZ,
    tombstone_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    UNIQUE (source, asset_namespace, source_asset_id)
);

CREATE TABLE source_file_assets (
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    asset_id BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    PRIMARY KEY (source_file_id, asset_id)
);

CREATE TABLE asset_versions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_id BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    content_fingerprint BYTEA NOT NULL CHECK (octet_length(content_fingerprint) = 32),
    fingerprint_version TEXT NOT NULL,
    normalized_payload JSONB,
    payload_fidelity TEXT NOT NULL CHECK (payload_fidelity IN ('full', 'fingerprint_only')),
    first_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    last_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    provider_updated_at TIMESTAMPTZ,
    authority_at TIMESTAMPTZ,
    authority_kind TEXT NOT NULL CHECK (
        authority_kind IN ('provider_revision', 'provider_updated_at', 'exported_at', 'unknown')
    ),
    lifecycle_state TEXT CHECK (lifecycle_state IN ('active', 'provider_deleted')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (asset_id, fingerprint_version, content_fingerprint),
    CHECK (payload_fidelity = 'fingerprint_only' OR normalized_payload IS NOT NULL)
);

CREATE TABLE asset_observations (
    import_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE RESTRICT,
    asset_id BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    asset_version_id BIGINT NOT NULL REFERENCES asset_versions(id) ON DELETE RESTRICT,
    raw_record_fingerprint BYTEA CHECK (
        raw_record_fingerprint IS NULL OR octet_length(raw_record_fingerprint) = 32
    ),
    outcome TEXT NOT NULL CHECK (
        outcome IN ('inserted', 'updated', 'unchanged', 'stale', 'conflicted', 'tombstoned')
    ),
    PRIMARY KEY (import_run_id, source_file_id, asset_id)
);

CREATE INDEX records_source_type_idx ON records (source, record_type);
CREATE INDEX records_occurred_at_idx ON records (occurred_at);
CREATE INDEX source_file_records_record_idx ON source_file_records (record_id);
CREATE INDEX record_versions_history_idx ON record_versions (record_id, created_at);
CREATE INDEX record_observations_record_idx ON record_observations (record_id, import_run_id);
CREATE INDEX assets_source_mime_idx ON assets (source, mime_type);
CREATE INDEX source_file_assets_asset_idx ON source_file_assets (asset_id);
CREATE INDEX asset_versions_history_idx ON asset_versions (asset_id, created_at);
CREATE INDEX asset_observations_asset_idx ON asset_observations (asset_id, import_run_id);

CREATE TABLE relationships (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    source_relationship_namespace TEXT NOT NULL,
    source_relationship_id TEXT NOT NULL,
    subject_kind TEXT NOT NULL CHECK (
        subject_kind IN ('conversation', 'event', 'record', 'asset')
    ),
    subject_source TEXT NOT NULL,
    subject_namespace TEXT NOT NULL,
    subject_source_id TEXT NOT NULL,
    object_kind TEXT NOT NULL CHECK (
        object_kind IN ('conversation', 'event', 'record', 'asset')
    ),
    object_source TEXT NOT NULL,
    object_namespace TEXT NOT NULL,
    object_source_id TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_fingerprint BYTEA NOT NULL CHECK (octet_length(content_fingerprint) = 32),
    fingerprint_version TEXT NOT NULL,
    provider_updated_at TIMESTAMPTZ,
    authority_at TIMESTAMPTZ,
    authority_kind TEXT NOT NULL CHECK (
        authority_kind IN ('provider_revision', 'provider_updated_at', 'exported_at', 'unknown')
    ),
    canonical_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    first_observed_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE RESTRICT,
    last_observed_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE RESTRICT,
    canonical_revision BIGINT NOT NULL DEFAULT 1 CHECK (canonical_revision > 0),
    canonical_updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK (
        lifecycle_state IN ('active', 'provider_deleted')
    ),
    tombstoned_at TIMESTAMPTZ,
    tombstone_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    UNIQUE (source, source_relationship_namespace, source_relationship_id)
);

CREATE TABLE source_file_relationships (
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    relationship_id BIGINT NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,
    PRIMARY KEY (source_file_id, relationship_id)
);

CREATE TABLE relationship_versions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    relationship_id BIGINT NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,
    content_fingerprint BYTEA NOT NULL CHECK (octet_length(content_fingerprint) = 32),
    fingerprint_version TEXT NOT NULL,
    normalized_payload JSONB,
    payload_fidelity TEXT NOT NULL CHECK (payload_fidelity IN ('full', 'fingerprint_only')),
    first_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    last_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    provider_updated_at TIMESTAMPTZ,
    authority_at TIMESTAMPTZ,
    authority_kind TEXT NOT NULL CHECK (
        authority_kind IN ('provider_revision', 'provider_updated_at', 'exported_at', 'unknown')
    ),
    lifecycle_state TEXT CHECK (lifecycle_state IN ('active', 'provider_deleted')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (relationship_id, fingerprint_version, content_fingerprint),
    CHECK (payload_fidelity = 'fingerprint_only' OR normalized_payload IS NOT NULL)
);

CREATE TABLE relationship_observations (
    import_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE RESTRICT,
    relationship_id BIGINT NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,
    relationship_version_id BIGINT NOT NULL REFERENCES relationship_versions(id) ON DELETE RESTRICT,
    raw_record_fingerprint BYTEA CHECK (
        raw_record_fingerprint IS NULL OR octet_length(raw_record_fingerprint) = 32
    ),
    outcome TEXT NOT NULL CHECK (
        outcome IN ('inserted', 'updated', 'unchanged', 'stale', 'conflicted', 'tombstoned')
    ),
    PRIMARY KEY (import_run_id, source_file_id, relationship_id)
);

CREATE INDEX relationships_source_type_idx
    ON relationships (source, relationship_type);
CREATE INDEX relationships_subject_idx
    ON relationships (subject_source, subject_namespace, subject_source_id);
CREATE INDEX relationships_object_idx
    ON relationships (object_source, object_namespace, object_source_id);
CREATE INDEX source_file_relationships_relationship_idx
    ON source_file_relationships (relationship_id);
CREATE INDEX relationship_versions_history_idx
    ON relationship_versions (relationship_id, created_at);
CREATE INDEX relationship_observations_relationship_idx
    ON relationship_observations (relationship_id, import_run_id);

-- Migration 008: full-text search. Mirrored here (not just in db/migrations/008_full_text_search.sql)
-- because this file is the fresh-install snapshot; the UPDATE backfills below are harmless no-ops
-- against an empty database.
--
-- Asset filenames are rarely sentences (opaque hex/numeric IDs, or human names joined with
-- underscores/hyphens, e.g. "sourdough_starter.jpg"). Postgres's default parser treats such a
-- filename as one opaque token, so a plain to_tsvector would never match "sourdough" alone.
-- Normalizing separators to spaces first makes filename search actually useful.
CREATE FUNCTION filename_search_text(value text) RETURNS text AS $$
    SELECT replace(replace(replace(value, '_', ' '), '-', ' '), '.', ' ');
$$ LANGUAGE sql IMMUTABLE;

ALTER TABLE conversations ADD COLUMN search_vector tsvector;
ALTER TABLE events ADD COLUMN search_vector tsvector;
ALTER TABLE records ADD COLUMN search_vector tsvector;
ALTER TABLE assets ADD COLUMN search_vector tsvector;

UPDATE conversations SET search_vector =
    setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
    setweight(to_tsvector('english', left(metadata::text, 10000)), 'D');

UPDATE events SET search_vector =
    setweight(to_tsvector('english', coalesce(text, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(actor_identity, '')), 'B') ||
    setweight(to_tsvector('english', event_type), 'C') ||
    setweight(to_tsvector('english', left(metadata::text, 10000)), 'D');

UPDATE records SET search_vector =
    setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(text, '')), 'B') ||
    setweight(to_tsvector('english', record_type), 'C') ||
    setweight(to_tsvector('english', left(metadata::text, 10000)), 'D');

UPDATE assets SET search_vector =
    setweight(to_tsvector('english', filename_search_text(filename)), 'A') ||
    setweight(to_tsvector('english', coalesce(mime_type, '')), 'C') ||
    setweight(to_tsvector('english', left(metadata::text, 10000)), 'D');

ALTER TABLE conversations ALTER COLUMN search_vector SET NOT NULL;
ALTER TABLE events ALTER COLUMN search_vector SET NOT NULL;
ALTER TABLE records ALTER COLUMN search_vector SET NOT NULL;
ALTER TABLE assets ALTER COLUMN search_vector SET NOT NULL;

CREATE INDEX conversations_search_vector_idx ON conversations USING GIN (search_vector);
CREATE INDEX events_search_vector_idx ON events USING GIN (search_vector);
CREATE INDEX records_search_vector_idx ON records USING GIN (search_vector);
CREATE INDEX assets_search_vector_idx ON assets USING GIN (search_vector);

CREATE FUNCTION conversations_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', coalesce(NEW.title, '')), 'A') ||
        setweight(to_tsvector('english', left(NEW.metadata::text, 10000)), 'D');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER conversations_search_vector_trigger
    BEFORE INSERT OR UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION conversations_search_vector_update();

CREATE FUNCTION events_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', coalesce(NEW.text, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(NEW.actor_identity, '')), 'B') ||
        setweight(to_tsvector('english', NEW.event_type), 'C') ||
        setweight(to_tsvector('english', left(NEW.metadata::text, 10000)), 'D');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER events_search_vector_trigger
    BEFORE INSERT OR UPDATE ON events
    FOR EACH ROW EXECUTE FUNCTION events_search_vector_update();

CREATE FUNCTION records_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', coalesce(NEW.title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(NEW.text, '')), 'B') ||
        setweight(to_tsvector('english', NEW.record_type), 'C') ||
        setweight(to_tsvector('english', left(NEW.metadata::text, 10000)), 'D');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER records_search_vector_trigger
    BEFORE INSERT OR UPDATE ON records
    FOR EACH ROW EXECUTE FUNCTION records_search_vector_update();

CREATE FUNCTION assets_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('english', filename_search_text(NEW.filename)), 'A') ||
        setweight(to_tsvector('english', coalesce(NEW.mime_type, '')), 'C') ||
        setweight(to_tsvector('english', left(NEW.metadata::text, 10000)), 'D');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER assets_search_vector_trigger
    BEFORE INSERT OR UPDATE ON assets
    FOR EACH ROW EXECUTE FUNCTION assets_search_vector_update();

INSERT INTO schema_migrations (version) VALUES (2), (3), (4), (5), (6), (7), (8) ON CONFLICT DO NOTHING;
