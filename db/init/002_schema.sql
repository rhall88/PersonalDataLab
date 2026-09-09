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
    UNIQUE (source, sha256)
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
    title TEXT,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (source, source_conversation_id)
);

CREATE TABLE IF NOT EXISTS events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at TIMESTAMPTZ,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_person_id BIGINT REFERENCES people(id) ON DELETE SET NULL,
    conversation_id BIGINT REFERENCES conversations(id) ON DELETE SET NULL,
    text TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_file_id BIGINT NOT NULL REFERENCES source_files(id) ON DELETE RESTRICT,
    source_record_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_file_id, source_record_id)
);

CREATE TABLE IF NOT EXISTS attachments (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    mime_type TEXT,
    path TEXT NOT NULL,
    sha256 TEXT CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
    file_size BIGINT CHECK (file_size IS NULL OR file_size >= 0),
    width INTEGER CHECK (width IS NULL OR width >= 0),
    height INTEGER CHECK (height IS NULL OR height >= 0),
    duration DOUBLE PRECISION CHECK (duration IS NULL OR duration >= 0),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS events_occurred_at_idx ON events (occurred_at);
CREATE INDEX IF NOT EXISTS events_source_type_idx ON events (source, event_type);
CREATE INDEX IF NOT EXISTS events_conversation_idx ON events (conversation_id, occurred_at);
