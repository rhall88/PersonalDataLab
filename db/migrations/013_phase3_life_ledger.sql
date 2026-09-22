-- Phase 3 of the derived meaning architecture (DERIVED_MEANING_ARCHITECTURE.md S15): the life
-- ledger. See PHASE3_IMPLEMENTATION_PLAN.md. No canonical table is altered by this migration.
--
-- metric_def/metric_dependency describe a named daily metric and the (source, object_kind)
-- pairs Phase 1's measure.coverage must already attest as covered before that metric's day can
-- be computed. Like derive.deriver (migration 010), these are code-declared and upserted by
-- `derive sync`, not hand-seeded here.
--
-- measure.metric_daily copies measure.coverage's own convention (migration 011): no
-- expected/unknown sentinel column, just a NOT NULL value keyed by (metric_id, bucket_start). A
-- day with genuine zero activity is a real row with value = 0; a day Phase 1 has not attested as
-- covered gets no row at all -- "unknown never renders as 0" is enforced by row absence, not by
-- a flag inside the row.
CREATE TABLE derive.metric_def (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    unit TEXT NOT NULL,
    code_version TEXT NOT NULL DEFAULT '1',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE derive.metric_dependency (
    metric_id BIGINT NOT NULL REFERENCES derive.metric_def(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    object_kind TEXT NOT NULL,
    PRIMARY KEY (metric_id, source, object_kind)
);

CREATE TABLE measure.metric_daily (
    metric_id BIGINT NOT NULL REFERENCES derive.metric_def(id) ON DELETE CASCADE,
    bucket_start DATE NOT NULL,
    value NUMERIC NOT NULL,
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    PRIMARY KEY (metric_id, bucket_start)
);

-- The compare CLI command scans a date range across every metric; this index makes that a range
-- scan instead of a sequential one as metric_daily grows.
CREATE INDEX metric_daily_bucket_idx ON measure.metric_daily (bucket_start);
