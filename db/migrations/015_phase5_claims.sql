-- Phase 5 of the derived meaning architecture (DERIVED_MEANING_ARCHITECTURE.md, sections 9 and
-- 15): the claims ledger. Wraps Phase 3/4's already-computed signal.anomaly and episode.episode
-- rows into inspectable know.claim objects with a per-dimension confidence profile and cited
-- evidence -- see PHASE5_IMPLEMENTATION_PLAN.md. No canonical table (events/records/etc.) is
-- altered by this migration, and no existing derive/measure/signal/episode table is altered
-- either: this phase reads them, it does not change their shape.

CREATE SCHEMA IF NOT EXISTS know;

-- One row per claim. `template`/`template_version` name exactly which claim template produced
-- it (decision 2 of the plan); `class` is assigned by that template, independent of whatever
-- final_inference tag the underlying deriver happens to carry (plan's "Corrections to prior
-- claims" -- signal_change_point/signal_anomaly are tagged COMPUTED even though the architecture's
-- own taxonomy calls a change point STATISTICAL; this table does not inherit that inconsistency).
-- `subject` is a small JSONB descriptor (e.g. {"metric": "..."} or {"episode_id": ...}) for
-- filtering/display; `confidence` is the dimension profile itself (plan decision 3), one key per
-- applicable dimension only -- an inapplicable dimension is simply absent from the object, never
-- a null standing in for "not computed".
CREATE TABLE know.claim (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    template TEXT NOT NULL CHECK (template IN ('communication_change', 'episode_evidence')),
    template_version TEXT NOT NULL DEFAULT '1',
    class TEXT NOT NULL CHECK (
        class IN ('OBSERVED', 'COMPUTED', 'STATISTICAL', 'SEMANTIC', 'DECLARED', 'HYPOTHESIS')
    ),
    statement TEXT NOT NULL,
    subject JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded')),
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- One row per cited evidence item. `evidence_table` names which table the evidence came from
-- ('signal.anomaly', 'signal.change_point', or 'episode.episode' today); `evidence_ref` is that
-- row's natural key, not a foreign key -- the same precedent episode.episode.evidence already
-- set in Phase 4, since every cited table here is a GLOBAL clear-and-replace deriver output with
-- no stable surrogate id to reference safely across re-runs. Role is one of the architecture's
-- four (section 9); this phase's evaluator only ever writes 'supports' (plan decision 4), but the
-- other three are real, reachable values a later phase's evaluator can start writing into this
-- same table without a schema change.
CREATE TABLE know.claim_evidence (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_id BIGINT NOT NULL REFERENCES know.claim(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('supports', 'contradicts', 'qualifies', 'contextualizes')),
    evidence_table TEXT NOT NULL,
    evidence_ref JSONB NOT NULL
);

CREATE INDEX claim_template_idx ON know.claim (template, class);
CREATE INDEX claim_evidence_claim_idx ON know.claim_evidence (claim_id);
