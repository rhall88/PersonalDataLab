-- Phase 6 (PHASE6_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md): text units and their local embeddings.
-- No canonical table (events/conversations/etc.) is altered by this migration; semantic.text_unit
-- is a pure derivation off canonical evidence, never referenced by a foreign key from any
-- canonical table -- this whole schema can be dropped and rebuilt from scratch (invariant 20),
-- the same precedent measure/signal/episode/know already established.

CREATE SCHEMA IF NOT EXISTS semantic;

-- One row per text unit (decision 2: 45-minute-gap sessions for facebook/imessages, existing
-- conversation_id boundaries for openai/anthropic, one unit per message for google mail).
-- event_ids is a plain array, not a join table: this project has no purge/invalidation machinery
-- to traverse it relationally yet (a real, pre-existing gap, not solved by this migration), and
-- an array already serves every read this phase's own CLI needs.
CREATE TABLE semantic.text_unit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    conversation_id BIGINT REFERENCES conversations(id),
    event_ids BIGINT[] NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,
    text TEXT NOT NULL,
    truncated BOOLEAN NOT NULL DEFAULT FALSE,
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX text_unit_source_started_idx ON semantic.text_unit (source, started_at);
CREATE INDEX text_unit_conversation_idx ON semantic.text_unit (conversation_id);

-- One embedding table per embedding space (the architecture's own naming pattern,
-- embedding_<space>), so a future model swap adds a table/index rather than migrating vector
-- width in place. BAAI/bge-small-en-v1.5 is 384-dimensional (decision 3).
CREATE TABLE semantic.embedding_bge_small_en_v15 (
    text_unit_id BIGINT PRIMARY KEY REFERENCES semantic.text_unit(id) ON DELETE CASCADE,
    vector vector(384) NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- cosine similarity is bge-small-en-v1.5's own recommended metric (decision 5).
CREATE INDEX embedding_bge_small_en_v15_hnsw_idx
    ON semantic.embedding_bge_small_en_v15
    USING hnsw (vector vector_cosine_ops);
