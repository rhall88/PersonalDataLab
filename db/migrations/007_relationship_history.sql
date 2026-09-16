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
