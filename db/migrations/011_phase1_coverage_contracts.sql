-- Phase 1 of the derived meaning architecture (DERIVED_MEANING_ARCHITECTURE.md): coverage,
-- contracts, and time. No canonical table is altered by this migration. Phase 0 already
-- confirmed (PHASE0_IMPLEMENTATION_PLAN.md) that as-of reconstruction needs no new columns on
-- events; this migration's own plan (PHASE1_IMPLEMENTATION_PLAN.md) re-confirmed that holds
-- uniformly across all five canonical object types by reading their DDL directly. So this
-- migration adds exactly two tables and nothing else: declare.source_contract (what a source can
-- observe) and measure.coverage (the completeness rollup Phase 1's deriver writes).

-- Per-source declaration of observability. gap_semantics distinguishes "the provider never
-- exported this" from "the provider deleted it" from "the device wasn't present" from "unknown"
-- (architecture inv. 3: every source declares what it can/cannot observe). default_timezone is
-- Phase 1's scoped-down time normalization: honored only when a source's own observations carry
-- no explicit offset -- full location-derived zone resolution is deferred to a later phase (see
-- PHASE1_IMPLEMENTATION_PLAN.md's Decisions). Unseeded here deliberately: contracts are declared
-- per source via the CLI during rollout, not hard-coded into a migration (same reasoning as
-- migration 010 leaving derive.deriver unseeded -- a migration shouldn't need rewriting every
-- time a declared fact changes).
CREATE TABLE declare.source_contract (
    source TEXT PRIMARY KEY,
    contract_version INTEGER NOT NULL DEFAULT 1,
    observable_kinds TEXT[] NOT NULL,
    covered_range_start DATE,
    covered_range_end DATE,
    gap_semantics TEXT NOT NULL CHECK (
        gap_semantics IN ('not_exported', 'deleted_by_provider', 'device_absent', 'unknown')
    ),
    default_timezone TEXT NOT NULL,
    notes TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        covered_range_end IS NULL OR covered_range_start IS NULL
        OR covered_range_end >= covered_range_start
    )
);

-- The Phase 1 deriver's output. Day grain only (Decisions: no second stored grain until a real
-- performance need shows up -- month is a query-side aggregation). expected and completeness are
-- NULL, not zero, when source_contract declares no covered_range for that period, so an
-- undeclared contract never produces a fabricated gap -- the same "no row/no value rather than a
-- false zero" convention Phase 0's activity derivers already use. No FK to source_contract: the
-- deriver reads it at compute time, the same way it reads any other evidence, rather than the
-- schema enforcing a declaration must exist first.
CREATE TABLE measure.coverage (
    source TEXT NOT NULL,
    bucket_start DATE NOT NULL,
    grain TEXT NOT NULL CHECK (grain = 'day'),
    object_kind TEXT NOT NULL,
    expected INTEGER,
    observed INTEGER NOT NULL,
    completeness NUMERIC,
    gap_reason TEXT CHECK (
        gap_reason IS NULL
        OR gap_reason IN ('not_exported', 'deleted_by_provider', 'device_absent', 'unknown')
    ),
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    PRIMARY KEY (source, bucket_start, object_kind)
);

-- bucket_start is the same UTC-vs-declared-timezone day boundary the as-of/time-normalization
-- helper resolves for both this table and event/record occurred_at comparisons, so a query
-- against this table shares the exact day semantics the rest of Phase 1 uses.
COMMENT ON COLUMN measure.coverage.bucket_start IS
    'Local calendar day per declare.source_contract.default_timezone, or an explicit '
    'per-observation offset when the source provides one. See PHASE1_IMPLEMENTATION_PLAN.md.';
