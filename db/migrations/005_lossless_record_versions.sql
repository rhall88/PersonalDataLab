ALTER TABLE import_runs
    ADD COLUMN policy_version TEXT NOT NULL DEFAULT 'cumulative-v1';

CREATE TABLE conversation_versions (
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

CREATE TABLE event_versions (
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

CREATE TABLE attachment_versions (
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

ALTER TABLE conversations
    ADD COLUMN first_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    ADD COLUMN last_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT;

ALTER TABLE events
    ADD COLUMN first_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    ADD COLUMN last_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT;

ALTER TABLE attachments
    ADD COLUMN first_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT,
    ADD COLUMN last_observed_run_id BIGINT REFERENCES import_runs(id) ON DELETE RESTRICT;

ALTER TABLE conversation_observations
    ADD COLUMN conversation_version_id BIGINT REFERENCES conversation_versions(id) ON DELETE RESTRICT;
ALTER TABLE event_observations
    ADD COLUMN event_version_id BIGINT REFERENCES event_versions(id) ON DELETE RESTRICT;
ALTER TABLE attachment_observations
    ADD COLUMN attachment_version_id BIGINT REFERENCES attachment_versions(id) ON DELETE RESTRICT;

ALTER TABLE conversation_observations
    DROP CONSTRAINT conversation_observations_outcome_check,
    ADD CONSTRAINT conversation_observations_outcome_check CHECK (
        outcome IN ('inserted', 'updated', 'unchanged', 'stale', 'conflicted', 'tombstoned')
    );
ALTER TABLE event_observations
    DROP CONSTRAINT event_observations_outcome_check,
    ADD CONSTRAINT event_observations_outcome_check CHECK (
        outcome IN ('inserted', 'updated', 'unchanged', 'stale', 'conflicted', 'tombstoned')
    );
ALTER TABLE attachment_observations
    DROP CONSTRAINT attachment_observations_outcome_check,
    ADD CONSTRAINT attachment_observations_outcome_check CHECK (
        outcome IN ('inserted', 'updated', 'unchanged', 'stale', 'conflicted', 'tombstoned')
    );

CREATE INDEX conversation_versions_history_idx
    ON conversation_versions (conversation_id, created_at);
CREATE INDEX event_versions_history_idx
    ON event_versions (event_id, created_at);
CREATE INDEX attachment_versions_history_idx
    ON attachment_versions (attachment_id, created_at);
