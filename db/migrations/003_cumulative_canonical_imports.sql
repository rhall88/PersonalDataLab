ALTER TABLE source_files
    ADD COLUMN last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;

UPDATE source_files SET last_seen_at = ingested_at;

CREATE TABLE source_file_locations (
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    original_path TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_file_id, original_path)
);

INSERT INTO source_file_locations
    (source_file_id, original_path, first_seen_at, last_seen_at)
SELECT id, original_path, ingested_at, ingested_at
FROM source_files;

CREATE TABLE import_runs (
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

CREATE TABLE import_run_source_files (
    import_run_id BIGINT NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE RESTRICT,
    category TEXT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (import_run_id, source_file_id)
);

ALTER TABLE conversations
    ADD COLUMN source_record_namespace TEXT,
    ADD COLUMN content_fingerprint BYTEA,
    ADD COLUMN fingerprint_version TEXT,
    ADD COLUMN provider_updated_at TIMESTAMPTZ,
    ADD COLUMN authority_at TIMESTAMPTZ,
    ADD COLUMN authority_kind TEXT NOT NULL DEFAULT 'unknown' CHECK (
        authority_kind IN ('provider_revision', 'provider_updated_at', 'exported_at', 'unknown')
    ),
    ADD COLUMN canonical_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    ADD COLUMN canonical_revision BIGINT NOT NULL DEFAULT 1 CHECK (canonical_revision > 0),
    ADD COLUMN canonical_updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK (
        lifecycle_state IN ('active', 'provider_deleted')
    ),
    ADD COLUMN tombstoned_at TIMESTAMPTZ,
    ADD COLUMN tombstone_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT;

UPDATE conversations
SET source_record_namespace = CASE
    WHEN source = 'openai' THEN 'chat'
    WHEN source = 'anthropic' AND metadata -> 'anthropic' ? 'design_chat_uuid'
        THEN 'design_chat'
    WHEN source = 'anthropic' THEN 'chat'
    ELSE 'conversation'
END;

ALTER TABLE events
    ADD COLUMN source_record_namespace TEXT,
    ADD COLUMN actor_identity TEXT,
    ADD COLUMN content_fingerprint BYTEA,
    ADD COLUMN fingerprint_version TEXT,
    ADD COLUMN provider_updated_at TIMESTAMPTZ,
    ADD COLUMN authority_at TIMESTAMPTZ,
    ADD COLUMN authority_kind TEXT NOT NULL DEFAULT 'unknown' CHECK (
        authority_kind IN ('provider_revision', 'provider_updated_at', 'exported_at', 'unknown')
    ),
    ADD COLUMN canonical_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    ADD COLUMN canonical_revision BIGINT NOT NULL DEFAULT 1 CHECK (canonical_revision > 0),
    ADD COLUMN canonical_updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK (
        lifecycle_state IN ('active', 'provider_deleted')
    ),
    ADD COLUMN tombstoned_at TIMESTAMPTZ,
    ADD COLUMN tombstone_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT;

UPDATE events
SET source_record_namespace = CASE
        WHEN source = 'openai' AND event_type = 'conversation_message' THEN 'message'
        WHEN source = 'anthropic' AND event_type = 'conversation_message'
             AND metadata -> 'anthropic' ? 'design_chat_uuid' THEN 'design_message'
        WHEN source = 'anthropic' AND event_type = 'conversation_message' THEN 'message'
        WHEN event_type = 'account_profile_snapshot' THEN 'account_profile'
        WHEN event_type = 'account_login' THEN 'account_login'
        WHEN event_type = 'project_snapshot' THEN 'project'
        WHEN event_type = 'project_document' THEN 'project_document'
        WHEN event_type = 'memory_snapshot' THEN 'memory'
        WHEN event_type = 'memory_file_snapshot' THEN 'memory_file'
        ELSE event_type
    END,
    actor_identity = CASE
        WHEN source = 'openai' THEN metadata ->> 'author_role'
        WHEN source = 'anthropic' THEN COALESCE(
            metadata -> 'anthropic' ->> 'source_sender',
            metadata -> 'anthropic' ->> 'role',
            metadata -> 'anthropic' ->> 'account_uuid'
        )
        ELSE NULL
    END;

ALTER TABLE attachments
    ADD COLUMN source_record_namespace TEXT,
    ADD COLUMN source_attachment_id TEXT,
    ADD COLUMN content_fingerprint BYTEA,
    ADD COLUMN fingerprint_version TEXT,
    ADD COLUMN provider_updated_at TIMESTAMPTZ,
    ADD COLUMN authority_at TIMESTAMPTZ,
    ADD COLUMN authority_kind TEXT NOT NULL DEFAULT 'unknown' CHECK (
        authority_kind IN ('provider_revision', 'provider_updated_at', 'exported_at', 'unknown')
    ),
    ADD COLUMN canonical_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    ADD COLUMN canonical_revision BIGINT NOT NULL DEFAULT 1 CHECK (canonical_revision > 0),
    ADD COLUMN canonical_updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK (
        lifecycle_state IN ('active', 'provider_deleted')
    ),
    ADD COLUMN tombstoned_at TIMESTAMPTZ,
    ADD COLUMN tombstone_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT;

UPDATE attachments
SET source_record_namespace = 'legacy_attachment',
    source_attachment_id = 'legacy:' || id::text;

CREATE TABLE conversation_observations (
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

CREATE TABLE event_observations (
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

CREATE TABLE attachment_observations (
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

CREATE INDEX conversation_observations_conversation_idx
    ON conversation_observations (conversation_id, import_run_id);
CREATE INDEX event_observations_event_idx
    ON event_observations (event_id, import_run_id);
CREATE INDEX attachment_observations_attachment_idx
    ON attachment_observations (attachment_id, import_run_id);
