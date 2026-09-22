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

-- Migration 009: reversible identity resolution. Mirrored here for the same reason as migration
-- 008 above -- this file is the fresh-install snapshot.
ALTER TABLE records ADD COLUMN actor_person_id BIGINT REFERENCES people(id) ON DELETE SET NULL;

CREATE TABLE identity_links (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id BIGINT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    identity_value TEXT NOT NULL,
    note TEXT,
    lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK (
        lifecycle_state IN ('active', 'reverted')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reverted_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX identity_links_active_idx
    ON identity_links (source, identity_value)
    WHERE lifecycle_state = 'active';

CREATE INDEX identity_links_person_id_idx ON identity_links (person_id);

-- Migration 010: derived meaning foundation (derive/declare/policy/measure schemas).
-- Mirrored here for the same reason as migrations 008 and 009 above -- this file is the
-- fresh-install snapshot.
-- Phase 0 of the derived meaning architecture (DERIVED_MEANING_ARCHITECTURE.md): the machinery
-- that lets derived data exist without ever touching canonical evidence. No canonical table is
-- altered by this migration -- everything lives in four new schemas.
--
-- The rest of this schema uses TEXT + CHECK for enum-like columns, and this migration follows
-- that convention with one deliberate exception: policy.egress_level is a native Postgres ENUM,
-- because Postgres orders enum values by declaration order, which lets GREATEST() compute the
-- egress lattice's max-restriction directly (architecture inv. 24, 29). That ordering is a real
-- dependency here, not a style choice, so a native type earns its keep.

CREATE SCHEMA derive;
CREATE SCHEMA declare;
CREATE SCHEMA policy;
CREATE SCHEMA measure;

CREATE TYPE policy.egress_level AS ENUM ('external_model_allowed', 'redactable', 'local_only');

-- An evidence epoch closes after a completed import command and records the highest import_runs
-- id it covers. Import runs are per-member (Google Takeout alone produces tens of thousands), so
-- they are too fine-grained for a version vector; an epoch is the unit "as of import N" actually
-- means. As-of reconstruction itself needs no new columns -- it replays *_versions/*_observations
-- with the existing authority rules (reconciliation.py) up to an epoch's max_import_run_id.
CREATE TABLE derive.evidence_epoch (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    max_import_run_id BIGINT NOT NULL REFERENCES import_runs(id),
    closed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note TEXT
);

CREATE INDEX evidence_epoch_max_import_run_id_idx
    ON derive.evidence_epoch (max_import_run_id);

-- A deriver is a named, registered computation. final_inference records its epistemic class
-- (architecture sec. 1) -- COMPUTED and STATISTICAL are the only classes Phase 0 derivers use;
-- the rest exist for later phases, but the CHECK is written for all seven now so no later
-- migration has to widen it.
CREATE TABLE derive.deriver (
    name TEXT PRIMARY KEY,
    output_relation TEXT NOT NULL,
    partition_class TEXT NOT NULL CHECK (partition_class IN ('LOCAL', 'HALO', 'GLOBAL')),
    lookback_halo INTERVAL NOT NULL DEFAULT '0',
    lookahead_halo INTERVAL NOT NULL DEFAULT '0',
    final_inference TEXT NOT NULL CHECK (final_inference IN (
        'OBSERVED', 'COMPUTED', 'STATISTICAL', 'SEMANTIC', 'DECLARED', 'HYPOTHESIS', 'NARRATIVE'
    )),
    description TEXT NOT NULL,
    CHECK (partition_class = 'HALO' OR (lookback_halo = '0' AND lookahead_halo = '0'))
);

-- version_hash = sha256(canonical_json({name, code_version, params, model_ref})). A deriver can
-- have many versions; at most one is 'active' at a time, and older versions stay queryable until
-- explicitly retired so a past answer can still be replayed against the version that produced it
-- (architecture inv. 31).
CREATE TABLE derive.deriver_version (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deriver_name TEXT NOT NULL REFERENCES derive.deriver(name),
    code_version TEXT NOT NULL,
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    model_ref JSONB,
    version_hash BYTEA NOT NULL UNIQUE CHECK (octet_length(version_hash) = 32),
    status TEXT NOT NULL CHECK (status IN ('shadow', 'active', 'retired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX deriver_version_deriver_name_idx ON derive.deriver_version (deriver_name);

CREATE UNIQUE INDEX deriver_version_one_active_idx
    ON derive.deriver_version (deriver_name) WHERE status = 'active';

-- Dependency edges between derivers, enforced acyclic at registration time in Python (a single
-- CHECK cannot forbid a multi-row cycle). This is the acyclic requirement architecture inv. 35
-- states for claims generally; Phase 0 has no claims yet, but the dependency graph plumbing is
-- shared, so the constraint against self-reference lives here from the start.
CREATE TABLE derive.deriver_dependency (
    upstream TEXT NOT NULL REFERENCES derive.deriver(name),
    downstream TEXT NOT NULL REFERENCES derive.deriver(name),
    PRIMARY KEY (upstream, downstream),
    CHECK (upstream <> downstream)
);

-- One row per (deriver_version, partition_key). This is the dirty/clean state the worker claims
-- and the scheduler marks -- the only mutable table in the derive schema.
CREATE TABLE derive.partition_state (
    deriver_version_id BIGINT NOT NULL REFERENCES derive.deriver_version(id),
    partition_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('dirty', 'running', 'clean', 'failed')),
    input_fingerprint BYTEA,
    dirty_since TIMESTAMPTZ,
    computed_at TIMESTAMPTZ,
    computed_epoch_id BIGINT REFERENCES derive.evidence_epoch(id),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    PRIMARY KEY (deriver_version_id, partition_key)
);

CREATE INDEX partition_state_dirty_idx
    ON derive.partition_state (deriver_version_id) WHERE state = 'dirty';

-- Append-only audit trail of every attempt, kept independent of partition_state so a failed or
-- skipped attempt is never silently lost when the partition later succeeds.
CREATE TABLE derive.derivation_run (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deriver_version_id BIGINT NOT NULL REFERENCES derive.deriver_version(id),
    partition_key TEXT NOT NULL,
    epoch_id BIGINT REFERENCES derive.evidence_epoch(id),
    input_fingerprint BYTEA,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'skipped_unchanged', 'failed')),
    rows_written BIGINT,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    stats JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX derivation_run_deriver_version_id_idx
    ON derive.derivation_run (deriver_version_id, partition_key);

-- The change feed's bookmark: the last import_runs.id whose observations have been read and
-- turned into dirty partitions. A single row per named cursor (Phase 0 needs exactly one).
CREATE TABLE derive.change_cursor (
    name TEXT PRIMARY KEY,
    last_import_run_id BIGINT NOT NULL DEFAULT 0
);

INSERT INTO derive.change_cursor (name, last_import_run_id) VALUES ('default', 0);

-- Declarations (architecture inv. 23): append-only, user-asserted facts. Never overwritten --
-- a correction supersedes the old row via `supersedes` rather than updating it in place, so the
-- full history of what you declared and when stays inspectable. Phase 0 creates this table
-- because policy.egress_override references it; nothing writes declarations of kind other than
-- 'egress_override' until later phases, but the CHECK lists the kinds the architecture already
-- commits to so it doesn't need widening for each one.
CREATE TABLE declare.declaration (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN (
        'episode', 'identity_same_as', 'identity_not_same_as', 'metric_definition',
        'label', 'egress_override', 'exclude', 'purge'
    )),
    payload JSONB NOT NULL,
    valid_during TSTZRANGE,
    supersedes BIGINT REFERENCES declare.declaration(id),
    declared_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX declaration_kind_idx ON declare.declaration (kind);

-- Egress policy (architecture sec. 7, inv. 24 and 29): what may leave the machine, kept as its
-- own versioned table. Named egress_policy_version, not policy_version, specifically so it is
-- never confused with the unrelated import_runs.policy_version (parser/import policy) -- the two
-- have nothing to do with each other and previously shared a name by accident.
CREATE TABLE policy.egress_policy_version (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    definition JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Per-source defaults for the version they were declared under. sensitivity is a free-form label
-- set (source-level granularity for now; finer-grained sensitivity arrives with metric
-- definitions in a later phase). egress is the lattice value a deriver reading this source
-- inherits unless an override says otherwise.
CREATE TABLE policy.source_policy (
    source TEXT NOT NULL,
    egress_policy_version_id BIGINT NOT NULL REFERENCES policy.egress_policy_version(id),
    sensitivity TEXT[] NOT NULL DEFAULT '{}',
    egress policy.egress_level NOT NULL DEFAULT 'local_only',
    PRIMARY KEY (source, egress_policy_version_id)
);

-- Loosening egress below what the lattice would otherwise compute always requires an override
-- tied to a declaration -- never automatic (architecture inv. 24: "loosening only via a recorded
-- egress_override tied to a declaration"). to_level < from_level enforces that this table can
-- only ever loosen, never tighten (tightening needs no override -- it's just the default).
CREATE TABLE policy.egress_override (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    object_ref TEXT NOT NULL,
    from_level policy.egress_level NOT NULL,
    to_level policy.egress_level NOT NULL,
    declaration_id BIGINT NOT NULL REFERENCES declare.declaration(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (to_level < from_level)
);

CREATE INDEX egress_override_object_ref_idx ON policy.egress_override (object_ref);

-- Seed: egress policy version 1, all six sources local_only (inv. 29 -- nothing leaves the
-- machine until explicitly authorized). Sensitivity defaults: intimate for the two sources that
-- carry personal/relationship messaging, private for the rest. Both are source-level starting
-- points, not a final answer, and either can be loosened later only via a declared override.
-- Single-user scope for Phase 0: nothing here is scoped by user, because there is only one.
INSERT INTO policy.egress_policy_version (definition)
VALUES ('{"note": "Phase 0 defaults: all sources local_only, seeded 2026-09-21"}'::jsonb);

INSERT INTO policy.source_policy (source, egress_policy_version_id, sensitivity, egress)
SELECT source, 1, sensitivity, 'local_only'
FROM (VALUES
    ('anthropic', ARRAY['private']::TEXT[]),
    ('openai', ARRAY['private']::TEXT[]),
    ('imessages', ARRAY['intimate']::TEXT[]),
    ('apple', ARRAY['private']::TEXT[]),
    ('google', ARRAY['private']::TEXT[]),
    ('facebook', ARRAY['intimate']::TEXT[])
) AS defaults(source, sensitivity);

-- The two Phase 0 derivers' output tables. Created here so `DROP SCHEMA measure CASCADE`
-- followed by a rebuild is the literal, testable form of "the derived layer is disposable"
-- (architecture inv. 20).
CREATE TABLE measure.source_activity_daily (
    source TEXT NOT NULL,
    day DATE NOT NULL,
    object_kind TEXT NOT NULL,
    active_count BIGINT NOT NULL,
    tombstoned_count BIGINT NOT NULL,
    versions BIGINT NOT NULL,
    sensitivity TEXT[] NOT NULL,
    egress policy.egress_level NOT NULL,
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    PRIMARY KEY (source, day, object_kind)
);

CREATE TABLE measure.source_activity_7d (
    source TEXT NOT NULL,
    day DATE NOT NULL,
    object_kind TEXT NOT NULL,
    active_count BIGINT NOT NULL,
    tombstoned_count BIGINT NOT NULL,
    versions BIGINT NOT NULL,
    sensitivity TEXT[] NOT NULL,
    egress policy.egress_level NOT NULL,
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    PRIMARY KEY (source, day, object_kind)
);

-- day is UTC for both tables, explicitly provisional until Phase 1 derives each source's local
-- time zone. Recorded here, not just in application code, so a query against these tables can't
-- silently assume local-time semantics that don't exist yet.
COMMENT ON COLUMN measure.source_activity_daily.day IS
    'UTC calendar day. Provisional -- see Phase 1 (time normalization) for local time.';
COMMENT ON COLUMN measure.source_activity_7d.day IS
    'UTC calendar day, trailing 7-day window ending on this day. Same provisional note as '
    'measure.source_activity_daily.day.';

-- derive.deriver and derive.deriver_dependency are deliberately NOT seeded here. A deriver's
-- definition (its halo, params, description) is code, registered by the @deriver decorator in
-- registry.py the first time it runs; a schema migration hard-coding that would need a new
-- migration every time a deriver's parameters change, defeating the point of having a registry.

-- Phase 1 of the derived meaning architecture (DERIVED_MEANING_ARCHITECTURE.md): coverage,
-- contracts, and time. No canonical table is altered by this migration. Phase 0 already
-- confirmed (PHASE0_IMPLEMENTATION_PLAN.md) that as-of reconstruction needs no new columns on
-- events; this migration's own plan (PHASE1_IMPLEMENTATION_PLAN.md) re-confirmed that holds
-- uniformly across all five canonical object types by reading their DDL directly. So this
-- migration adds exactly two tables and nothing else: declare.source_contract (what a source can
-- observe) and measure.coverage (the completeness rollup Phase 1's deriver writes).

-- Per-source declaration of observability. gap_semantics distinguishes "the provider never
-- exported this" from "the provider deleted it" from "the device wasn't present" from "unknown"
-- (architecture inv. 3: every source declares what it can/cannot observe). default_timezone is
-- Phase 1's scoped-down time normalization: honored only when a source's own observations carry
-- no explicit offset -- full location-derived zone resolution is deferred to a later phase (see
-- PHASE1_IMPLEMENTATION_PLAN.md's Decisions). Unseeded here deliberately: contracts are declared
-- per source via the CLI during rollout, not hard-coded into a migration (same reasoning as
-- migration 010 leaving derive.deriver unseeded -- a migration shouldn't need rewriting every
-- time a declared fact changes).
CREATE TABLE declare.source_contract (
    source TEXT PRIMARY KEY,
    contract_version INTEGER NOT NULL DEFAULT 1,
    observable_kinds TEXT[] NOT NULL,
    covered_range_start DATE,
    covered_range_end DATE,
    gap_semantics TEXT NOT NULL CHECK (
        gap_semantics IN ('not_exported', 'deleted_by_provider', 'device_absent', 'unknown')
    ),
    default_timezone TEXT NOT NULL,
    notes TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        covered_range_end IS NULL OR covered_range_start IS NULL
        OR covered_range_end >= covered_range_start
    )
);

-- The Phase 1 deriver's output. Day grain only (Decisions: no second stored grain until a real
-- performance need shows up -- month is a query-side aggregation). expected and completeness are
-- NULL, not zero, when source_contract declares no covered_range for that period, so an
-- undeclared contract never produces a fabricated gap -- the same "no row/no value rather than a
-- false zero" convention Phase 0's activity derivers already use. No FK to source_contract: the
-- deriver reads it at compute time, the same way it reads any other evidence, rather than the
-- schema enforcing a declaration must exist first.
CREATE TABLE measure.coverage (
    source TEXT NOT NULL,
    bucket_start DATE NOT NULL,
    grain TEXT NOT NULL CHECK (grain = 'day'),
    object_kind TEXT NOT NULL,
    expected INTEGER,
    observed INTEGER NOT NULL,
    completeness NUMERIC,
    gap_reason TEXT CHECK (
        gap_reason IS NULL
        OR gap_reason IN ('not_exported', 'deleted_by_provider', 'device_absent', 'unknown')
    ),
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    PRIMARY KEY (source, bucket_start, object_kind)
);

-- bucket_start is the same UTC-vs-declared-timezone day boundary the as-of/time-normalization
-- helper resolves for both this table and event/record occurred_at comparisons, so a query
-- against this table shares the exact day semantics the rest of Phase 1 uses.
COMMENT ON COLUMN measure.coverage.bucket_start IS
    'Local calendar day per declare.source_contract.default_timezone, or an explicit '
    'per-observation offset when the source provides one. See PHASE1_IMPLEMENTATION_PLAN.md.';


-- Phase 2 of the derived meaning architecture (DERIVED_MEANING_ARCHITECTURE.md, section 5):
-- identity resolution. No canonical table (events/records/etc.) is altered by this migration.
-- Migration 009 already added `people` and `identity_links` -- a working, reversible, purely
-- manual instance of the architecture's tier 3 (You/DECLARED). This migration adds the
-- append-only log underneath it (PHASE2_IMPLEMENTATION_PLAN.md's "the log is the source of
-- truth; clusters are a derived, versioned view") and the scaffolding tiers 1 and 2 need, without
-- redesigning identity_links itself, which already has the right reversibility semantics.

CREATE SCHEMA IF NOT EXISTS identity;

-- One resolution_version per identity-changing operation (a CLI merge/split call, or one
-- rules-tier run), not per identity.assertion row -- a single operation that writes several
-- assertion rows (e.g. a rules-tier pass linking multiple groups) can share one version number
-- instead of each row claiming its own, without a later redesign of this column.
CREATE SEQUENCE identity.resolution_version_seq;

-- The append-only log identity_links was missing: every merge, split, and negative declaration,
-- with which tier made it and why. identity_links remains the fast current-state read path
-- (unchanged unique-active-per-(source, identity_value) index from migration 009); this table is
-- what identity_links' lifecycle_state transitions are now derived from, and what the rules tier
-- and identity-not-same-as actually consult before acting.
CREATE TABLE identity.assertion (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    resolution_version BIGINT NOT NULL,
    tier TEXT NOT NULL CHECK (tier IN ('computed', 'declared')),
    action TEXT NOT NULL CHECK (action IN ('link', 'unlink', 'not_same_as')),
    source TEXT NOT NULL,
    identity_value TEXT NOT NULL,
    person_id BIGINT REFERENCES people(id) ON DELETE SET NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX identity_assertion_source_value_idx
    ON identity.assertion (source, identity_value);

-- A permanent negative declaration: this (source, identity_value) is never the same person as
-- person_id (or, with person_id NULL, is never auto-linked to anything by the rules tier at
-- all). Required before any auto-linking exists -- PHASE2_IMPLEMENTATION_PLAN.md's whole
-- rules-tier design depends on this being checkable before a link is written, not bolted on
-- after the first wrong auto-link is made.
CREATE TABLE identity.not_same_as (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    identity_value TEXT NOT NULL,
    person_id BIGINT REFERENCES people(id) ON DELETE CASCADE,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source, identity_value, person_id)
);

-- Which tier made the currently-active link, and which resolution_version it came from --
-- so a reader of identity_links alone (without joining identity.assertion) can already tell a
-- rules-tier auto-link from a human's declared one. Existing rows (migration 009's own two real
-- links, both made by hand before this migration existed) default to 'declared', which is the
-- accurate classification for every row written before the rules tier existed.
--
-- IF NOT EXISTS here (unlike the plain CREATEs above): identity_links is the one object this
-- migration touches that lives outside the fresh `identity` schema, so it isn't reset by
-- anything that drops and recreates that schema. tests/test_migrations.py's pr2_engine fixture
-- does exactly that between tests -- it drops+recreates `identity` for isolation, then replays
-- this whole migration file against the shared database to restore it, and a second replay must
-- not fail just because the column already exists on the one pre-existing table involved.
ALTER TABLE identity_links ADD COLUMN IF NOT EXISTS resolution_version BIGINT;
ALTER TABLE identity_links ADD COLUMN IF NOT EXISTS tier TEXT NOT NULL DEFAULT 'declared'
    CHECK (tier IN ('computed', 'declared'));

-- Phase 3 of the derived meaning architecture (DERIVED_MEANING_ARCHITECTURE.md S15): the life
-- ledger. See PHASE3_IMPLEMENTATION_PLAN.md. No canonical table is altered by this migration.
--
-- metric_def/metric_dependency describe a named daily metric and the (source, object_kind)
-- pairs Phase 1's measure.coverage must already attest as covered before that metric's day can
-- be computed. Like derive.deriver (migration 010), these are code-declared and upserted by
-- `derive sync`, not hand-seeded here.
--
-- measure.metric_daily copies measure.coverage's own convention (migration 011): no
-- expected/unknown sentinel column, just a NOT NULL value keyed by (metric_id, bucket_start). A
-- day with genuine zero activity is a real row with value = 0; a day Phase 1 has not attested as
-- covered gets no row at all -- "unknown never renders as 0" is enforced by row absence, not by
-- a flag inside the row.
CREATE TABLE derive.metric_def (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    unit TEXT NOT NULL,
    code_version TEXT NOT NULL DEFAULT '1',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE derive.metric_dependency (
    metric_id BIGINT NOT NULL REFERENCES derive.metric_def(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    object_kind TEXT NOT NULL,
    PRIMARY KEY (metric_id, source, object_kind)
);

CREATE TABLE measure.metric_daily (
    metric_id BIGINT NOT NULL REFERENCES derive.metric_def(id) ON DELETE CASCADE,
    bucket_start DATE NOT NULL,
    value NUMERIC NOT NULL,
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    PRIMARY KEY (metric_id, bucket_start)
);

-- The compare CLI command scans a date range across every metric; this index makes that a range
-- scan instead of a sequential one as metric_daily grows.
CREATE INDEX metric_daily_bucket_idx ON measure.metric_daily (bucket_start);

CREATE SCHEMA IF NOT EXISTS signal;
CREATE SCHEMA IF NOT EXISTS episode;

-- Two of the architecture's six named baseline kinds (decision 2): a rolling window over the most
-- recent known observations, and an expanding lifetime window. Keyed off derive.metric_def so this
-- rides Phase 3's existing metric catalog rather than inventing a second one.
CREATE TABLE signal.baseline (
    metric_id BIGINT NOT NULL REFERENCES derive.metric_def(id) ON DELETE CASCADE,
    bucket_start DATE NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('trailing_90d', 'lifetime')),
    center DOUBLE PRECISION NOT NULL,
    spread DOUBLE PRECISION NOT NULL,
    n_obs INTEGER NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (metric_id, bucket_start, kind)
);

-- PELT (ruptures) change points per metric over its full known-value history (decision 3).
-- Recomputed in full on every derive run --global, so this is a clear-and-replace table per
-- metric, not an append-only log.
CREATE TABLE signal.change_point (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    metric_id BIGINT NOT NULL REFERENCES derive.metric_def(id) ON DELETE CASCADE,
    at_date DATE NOT NULL,
    method TEXT NOT NULL DEFAULT 'pelt',
    computed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (metric_id, at_date, method)
);

-- Robust z-score against the trailing_90d baseline, stored only where |z| >= 3.0 (decision 4) --
-- this table holds anomalies, not a row for every known day.
CREATE TABLE signal.anomaly (
    metric_id BIGINT NOT NULL REFERENCES derive.metric_def(id) ON DELETE CASCADE,
    bucket_start DATE NOT NULL,
    observed DOUBLE PRECISION NOT NULL,
    baseline_kind TEXT NOT NULL,
    expected_low DOUBLE PRECISION NOT NULL,
    expected_high DOUBLE PRECISION NOT NULL,
    z_score DOUBLE PRECISION NOT NULL,
    method TEXT NOT NULL DEFAULT 'robust_z',
    computed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (metric_id, bucket_start)
);

-- Per-(source, correspondent, month) interaction metrics, scoped to facebook + imessages 1:1
-- threads only (decision 9). Keyed by raw (source, correspondent) rather than a metric id --
-- correspondents are not metrics, so there is no derive.metric_def row to reuse for them.
CREATE TABLE measure.interaction_dynamics (
    source TEXT NOT NULL,
    correspondent TEXT NOT NULL,
    bucket_start DATE NOT NULL,
    messages_in INTEGER NOT NULL,
    messages_out INTEGER NOT NULL,
    n_conversations INTEGER NOT NULL,
    reciprocity_index DOUBLE PRECISION NOT NULL,
    initiation_share DOUBLE PRECISION,
    median_response_latency_seconds DOUBLE PRECISION,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source, correspondent, bucket_start)
);

-- PELT change-point segmentation of each correspondent's monthly reciprocity_index series
-- (decision 10). Each state is described purely by the mean of every interaction metric over its
-- span -- no LLM labeling.
CREATE TABLE signal.interaction_state (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    correspondent TEXT NOT NULL,
    state_start DATE NOT NULL,
    state_end DATE NOT NULL,
    mean_messages_in DOUBLE PRECISION NOT NULL,
    mean_messages_out DOUBLE PRECISION NOT NULL,
    mean_reciprocity_index DOUBLE PRECISION NOT NULL,
    mean_initiation_share DOUBLE PRECISION,
    mean_median_response_latency_seconds DOUBLE PRECISION,
    n_months INTEGER NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source, correspondent, state_start)
);

-- Episodes: DECLARED (a person names a period; start/end are exact, so the range collapses to a
-- single point) and DETECTED (cross-source-family change-point clustering, decision 7; boundary is
-- genuinely uncertain, so the range is the cluster's min/max date). DERIVED is out of scope for
-- this phase (decision 8).
CREATE TABLE episode.episode (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('declared', 'derived', 'detected')),
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'confirmed', 'rejected')),
    start_range_low DATE NOT NULL,
    start_range_high DATE NOT NULL,
    end_range_low DATE NOT NULL,
    end_range_high DATE NOT NULL,
    label TEXT,
    note TEXT,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX interaction_dynamics_bucket_idx ON measure.interaction_dynamics (bucket_start);
CREATE INDEX change_point_metric_idx ON signal.change_point (metric_id, at_date);
CREATE INDEX episode_kind_status_idx ON episode.episode (kind, status);

INSERT INTO schema_migrations (version) VALUES (2), (3), (4), (5), (6), (7), (8), (9), (10), (11), (12), (13), (14) ON CONFLICT DO NOTHING;
