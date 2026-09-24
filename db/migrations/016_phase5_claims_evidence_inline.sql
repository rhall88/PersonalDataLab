-- Correction to migration 015, discovered while building the runtime that populates it
-- (PHASE5_IMPLEMENTATION_PLAN.md's Runtime section). This project's deriver framework
-- (derive/worker.py's _write_rows) writes ComputeResult.rows to exactly one output_relation per
-- deriver, via a plain multi-row INSERT with no RETURNING -- there is no way for one deriver's
-- compute() to both insert know.claim rows and know.claim_evidence rows that reference the
-- newly-generated know.claim.id values, since that id is only known after the INSERT the worker
-- itself performs, not before. episode.episode already solved exactly this problem for Phase 4's
-- episode evidence by storing it inline as a JSONB column rather than a child table; this
-- migration applies that same, already-proven fix here instead of reworking the framework.
--
-- know.claim_evidence has never been populated (migration 015 shipped with no runtime attached),
-- so this is a structural correction, not a data migration -- nothing to backfill.

ALTER TABLE know.claim ADD COLUMN evidence JSONB NOT NULL DEFAULT '{}'::jsonb;

DROP TABLE know.claim_evidence;
