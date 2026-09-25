-- Phase 8 (PHASE8_DOCUMENT_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md): brings records' genuinely
-- prose-like content (Drive documents, NotebookLM notebooks/sources, Recorder transcripts, Apple
-- Notes) into the semantic layer semantic-search/embeddings already read. A parallel table to
-- semantic.text_unit/semantic.embedding_bge_small_en_v15, not a reused/widened one (decision 2):
-- semantic.text_unit.event_ids has no meaningful value for a record-sourced unit, and overloading
-- one array column to mean two different things depending on source is exactly the kind of
-- ambiguous shared-column design this project's provenance-citation discipline argues against.

-- started_at/ended_at are nullable, not NOT NULL like semantic.text_unit's own columns:
-- Nearly all of the real in-scope records population has no occurred_at/created_at/updated_at at all
-- (PHASE8 plan grounding), and fabricating a placeholder timestamp to satisfy a NOT NULL
-- constraint would be exactly the kind of invented value this project's invariant 2 ("unknown is
-- never silently converted to zero/absent") argues against.
CREATE TABLE semantic.text_unit_document (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    record_id BIGINT NOT NULL REFERENCES records(id),
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    text TEXT NOT NULL,
    truncated BOOLEAN NOT NULL DEFAULT FALSE,
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (record_id)
);

CREATE INDEX text_unit_document_source_started_idx ON semantic.text_unit_document (source, started_at);

CREATE TABLE semantic.embedding_bge_small_en_v15_document (
    text_unit_document_id BIGINT PRIMARY KEY
        REFERENCES semantic.text_unit_document(id) ON DELETE CASCADE,
    vector vector(384) NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX embedding_bge_small_en_v15_document_hnsw_idx
    ON semantic.embedding_bge_small_en_v15_document
    USING hnsw (vector vector_cosine_ops);
