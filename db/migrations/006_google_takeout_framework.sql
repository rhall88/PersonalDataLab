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
