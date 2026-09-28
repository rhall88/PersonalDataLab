-- Phase 9 (PHASE9_TOPIC_CLUSTERING_IMPLEMENTATION_PLAN.md): unsupervised topic clustering over
-- Phase 6's real embeddings. No canonical table is altered; this is a pure derivation, fully
-- disposable per invariant 20, same as every other semantic/signal/measure/know table.
--
-- One table, not the originally-planned two (a cluster-metadata table plus a per-unit assignment
-- table) -- the framework's derive/worker.py writes exactly one output_relation per compute()
-- call, so the cluster's TF-IDF label is denormalized onto every member row instead of factored
-- into a separate table (same fix Phase 5's inline-JSONB evidence column already established for
-- this same constraint). A cluster's centroid is not persisted -- cheaply recomputable on demand
-- via AVG over semantic.embedding_bge_small_en_v15.vector for a cluster's members, not worth a
-- redundant 384-dim column on every one of ~70,145 rows.

CREATE TABLE semantic.topic_cluster_assignment (
    text_unit_id BIGINT NOT NULL REFERENCES semantic.text_unit(id) ON DELETE CASCADE,
    deriver_version_id BIGINT NOT NULL REFERENCES derive.deriver_version(id),
    cluster_index INTEGER NOT NULL,  -- -1 is HDBSCAN's reserved "noise" label, never a real topic
    cluster_label TEXT NOT NULL,     -- top-5 TF-IDF terms, comma-joined; 'noise' for cluster_index=-1
    distance_to_centroid DOUBLE PRECISION,  -- NULL for noise-labeled units (no centroid to measure)
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (text_unit_id, deriver_version_id)
);

CREATE INDEX topic_cluster_assignment_cluster_idx
    ON semantic.topic_cluster_assignment (deriver_version_id, cluster_index);
