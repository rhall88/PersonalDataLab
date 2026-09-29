-- UI v1: saved investigations. Derived tables are cleared and rewritten on recompute, so a saved
-- epoch id can detect drift but not reproduce the old answer; each revision therefore stores the
-- complete answer (InsightSpec) and an evidence snapshot of canonical ids, and a refresh compares
-- a re-run against it rather than replacing it.

CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE app.investigation (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title TEXT NOT NULL CHECK (btrim(title) <> ''),
    question TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE app.investigation_revision (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    investigation_id BIGINT NOT NULL REFERENCES app.investigation(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    plan JSONB NOT NULL,
    insight JSONB NOT NULL,
    evidence_snapshot JSONB NOT NULL,
    versions JSONB NOT NULL,
    epoch_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (investigation_id, revision)
);
