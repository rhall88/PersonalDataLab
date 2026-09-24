-- OCR/transcription (ROADMAP.md item 6, "Optional media enrichment"): a separately versioned
-- derived layer for local audio transcription, deliberately its own schema from Phase 6's
-- `semantic` (item 5) -- item 6 was always explicitly separate scope in the roadmap. No canonical
-- table (assets/etc.) is altered by this migration; media.transcript is a pure derivation off
-- canonical asset bytes, never referenced by a foreign key from any canonical table -- this whole
-- schema can be dropped and rebuilt from scratch (invariant 20), the same precedent every other
-- derived-meaning schema in this project already established.

CREATE SCHEMA IF NOT EXISTS media;

-- One row per audio asset (at most -- assets are not always readable or not always audio in
-- content despite their declared mime_type, and Whisper can fail on a genuinely corrupt/silent
-- file; `status` records which of those happened rather than silently omitting a row, matching
-- invariant 2's "unknown is never silently converted to zero/absent").
CREATE TABLE media.transcript (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    asset_id BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    model TEXT NOT NULL,
    language TEXT,
    text TEXT NOT NULL DEFAULT '',
    duration_seconds DOUBLE PRECISION,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'unreadable')),
    error TEXT,
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (asset_id)
);

CREATE INDEX transcript_source_idx ON media.transcript (source);
CREATE INDEX transcript_status_idx ON media.transcript (status);
