-- Audio de-duplication: keep one copy of every recording, the most complete one. The same sound
-- turns up many times (a take saved in MOTIV, trimmed in Audio Editor, exported to Files, backed
-- up by Apple and by Google), and byte hashes miss every copy that was trimmed or re-encoded, so
-- this compares the audio itself. Derived and droppable like the rest of `media`: no canonical
-- table is altered, nothing is deleted, and a superseded asset stays in `assets` as provenance.

-- One acoustic fingerprint per readable audio asset: 32-bit sub-fingerprints (band-energy
-- difference signs, 8 kHz mono, 31.25 per second), with a parallel voiced-frame mask so silence
-- never counts as a match.
CREATE TABLE media.audio_fingerprint (
    asset_id BIGINT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    sha256 TEXT,
    fingerprint_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'unreadable')),
    error TEXT,
    duration_seconds DOUBLE PRECISION,
    voiced_seconds DOUBLE PRECISION,
    codec TEXT,
    sample_rate INTEGER,
    channels INTEGER,
    codes BYTEA,
    voiced BYTEA,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- One row per asset that another asset makes redundant. `relation`:
--   exact_duplicate  identical bytes (same sha256)
--   same_audio       the same recording in another encoding or container
--   contained_in     this recording is a trimmed or partial copy of `kept_asset_id`
-- `kept_asset_id` is always the final keeper (chains are resolved), never itself superseded.
CREATE TABLE media.audio_supersession (
    asset_id BIGINT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
    kept_asset_id BIGINT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    relation TEXT NOT NULL CHECK (relation IN ('exact_duplicate', 'same_audio', 'contained_in')),
    coverage DOUBLE PRECISION,
    offset_seconds DOUBLE PRECISION,
    bit_error_rate DOUBLE PRECISION,
    detector_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (asset_id <> kept_asset_id)
);

CREATE INDEX audio_supersession_kept_idx ON media.audio_supersession (kept_asset_id);
