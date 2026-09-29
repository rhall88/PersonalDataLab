-- Phase 10 real-rollout fix: a text unit whose grammar-constrained envelope hits the generation
-- token cap yields truncated, unparseable JSON. Raising the cap can't rule that out (the grammar's
-- bounded strings/arrays still permit envelopes far longer than any practical cap), and raising
-- inside compute() fails the unit's whole source-month partition. 72 of ~65.5k real units hit
-- this, which would have blocked 41 percent of the corpus from applying. (No percent sign here:
-- the migrator runs this file through psycopg, which parses it as a placeholder even in comments.)
--
-- A truncated unit instead gets an explicit 'truncated' row with every model-derived field NULL:
-- never the 'none'/'[]' defaults, which would falsely assert "nothing found" rather than
-- "extraction incomplete". Downstream derivers read only 'complete' rows.

ALTER TABLE generative.extraction
    ADD COLUMN extraction_status TEXT NOT NULL DEFAULT 'complete'
        CHECK (extraction_status IN ('complete', 'truncated'));

ALTER TABLE generative.extraction ALTER COLUMN salience DROP NOT NULL;
ALTER TABLE generative.extraction ALTER COLUMN entity_mentions DROP NOT NULL;
ALTER TABLE generative.extraction ALTER COLUMN relation_candidates DROP NOT NULL;
ALTER TABLE generative.extraction ALTER COLUMN decision_state DROP NOT NULL;
ALTER TABLE generative.extraction ALTER COLUMN open_loop_state DROP NOT NULL;

ALTER TABLE generative.extraction ADD CONSTRAINT extraction_status_fields_check CHECK (
    (
        extraction_status = 'complete'
        AND salience IS NOT NULL
        AND entity_mentions IS NOT NULL
        AND relation_candidates IS NOT NULL
        AND decision_state IS NOT NULL
        AND open_loop_state IS NOT NULL
    )
    OR (
        extraction_status = 'truncated'
        AND interaction_primary IS NULL
        AND interaction_secondary IS NULL
        AND salience IS NULL
        AND entity_mentions IS NULL
        AND relation_candidates IS NULL
        AND event_type IS NULL
        AND event_summary IS NULL
        AND event_actor_slot IS NULL
        AND event_object_slot IS NULL
        AND event_location_slot IS NULL
        AND event_time_expression IS NULL
        AND decision_state IS NULL
        AND decision_summary IS NULL
        AND open_loop_state IS NULL
        AND open_loop_summary IS NULL
    )
);
