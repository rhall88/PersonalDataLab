# Phase 1 implementation plan: coverage, contracts, time

## Summary

Phase 0 built the derivation runtime itself (registry, versioning, change feed, partition
scheduling, egress policy) and proved it against the real canonical database. Phase 1, per
`DERIVED_MEANING_ARCHITECTURE.md`'s phased plan (section 15), is the next rung: make it possible
to ask "what did I know as of import N" and "how complete is my record of each source, month by
month" -- without touching a single canonical row.

The headline finding of this plan mirrors Phase 0's own opening move. Phase 0's plan corrected
the architecture doc's proposal to add `arrived_in_import`/`superseded_in_import` columns to the
evidence tables, pointing out that the existing `*_versions`/`*_observations` machinery already
is the append-only revision log section 18 calls for. This plan confirms that correction holds
uniformly across all five canonical object types, not just `events` where it was first checked:
`conversations`, `events`, `records`, `assets`, and `relationships` all carry
`first_observed_run_id`, `last_observed_run_id`, `canonical_revision`, `lifecycle_state`,
`tombstoned_at`, and `tombstone_run_id`, and each one's matching `*_versions` table carries
`first_observed_run_id`/`last_observed_run_id`/`lifecycle_state` per content-fingerprinted
version. As-of reconstruction for any object, at any historical import cutoff, is therefore a
pure read-side problem: a SQL function parameterized by a cutoff `import_runs.id`, built entirely
from columns that already exist. Phase 1 needs zero canonical schema changes. Everything it adds
is new tables in `declare`/`measure` plus one more LOCAL deriver riding Phase 0's existing
machinery.

## What the repository already provides

- **The revision log.** Confirmed this session by reading every one of the five canonical
  tables' and five `*_versions` tables' DDL in `db/init/002_schema.sql`: `conversations`,
  `events`, `records`, `assets`, `relationships` each have `first_observed_run_id NOT NULL`,
  `last_observed_run_id NOT NULL`, `canonical_revision`, `lifecycle_state CHECK (IN ('active',
  'provider_deleted'))`, `tombstoned_at`, `tombstone_run_id`. Each object's `*_versions` table
  (one immutable row per distinct `content_fingerprint`) carries its own
  `first_observed_run_id`/`last_observed_run_id`/`lifecycle_state`. `*_observations` records one
  row per `(object, import_run)` with an `outcome` in `inserted/updated/unchanged/stale/
  conflicted/tombstoned`. This is uniform across all five object kinds -- there is no
  special-cased object that needs different treatment.
- **A total order over "as of."** `import_runs.id` is already the append-only, monotonic axis
  every one of those columns is expressed in terms of. "As of import run N" needs no new concept:
  an object existed as of N iff `first_observed_run_id <= N`; it's tombstoned as of N iff
  `tombstone_run_id IS NOT NULL AND tombstone_run_id <= N`; its content as of N is the
  `*_versions` row with the greatest `first_observed_run_id <= N`.
  `reconciliation.py`'s authority-based (not ingestion-order) reconciliation already writes these
  columns correctly on every import, so no ingestion-path change is needed to make as-of queries
  meaningful.
- **The derivation runtime.** Phase 0's `derive/` package (registry, change feed reading only
  `*_observations`, halo-aware partition scheduling, `SKIP LOCKED` worker, egress lattice) is
  live and proven against the full 13GB database. A coverage rollup is exactly the shape of thing
  it was built for: a new LOCAL deriver, partitioned `source:YYYY-MM`, no new infrastructure.
- **`policy.source_policy` as a placement precedent.** Phase 0 already has a per-source
  declared-configuration table in the `policy` schema. `declare.declaration` is the other
  existing per-source-ish declared-fact table. `source_contract` goes in `declare` (see
  Decisions below): it's a declared fact about what a source can observe, not an
  access-control rule.

## Corrections to the architecture doc

1. **Section 3's `arrived_in_import`/`superseded_in_import`/`provenance_family_id` additions to
   the evidence schema are unnecessary**, confirmed now across all five canonical object types,
   not just `events`. As-of reconstruction is a read-side SQL problem over existing columns.
   Section 18's characterization of these columns as "performance projections" of the true
   append-only log undersells it: the log itself (`*_versions` + `*_observations`) is already
   there, so there is no projection to build, only queries to write.
2. **`provenance_family_id` should not be a column yet.** The architecture doc's provenance model
   (section 9) wants evidence grouped into independence families for confidence scoring. Today,
   every source in this database is exactly one family (Facebook is one pipeline, Apple is one
   pipeline, etc.) -- there is no case yet of two independent collection paths feeding the same
   `source`. Adding a column to carry a value that is always `source` itself is premature; a
   default-one-family-per-source convention, revisited when a real multi-family source shows up,
   avoids a schema change and a backfill for a distinction that doesn't exist in the data yet.
3. **Section 15's phase-1 usable-result framing ("coverage heatmap of your life by source and
   month") undersells what falls out for free**: once `source_contract` exists and coverage is a
   deriver output, "as of" queries and coverage share the same cutoff-by-`import_runs.id`
   primitive. Worth stating explicitly in the doc as one mechanism, not two.

## Scope

**In:**
- As-of reconstruction: a parameterized read path (SQL, invoked from Python -- see below) that
  reconstructs any of the five canonical object tables' visible rows and content as of a given
  `import_runs.id` cutoff.
- `declare.source_contract`: per-source declaration of `observable_kinds`, `covered_range`,
  `gap_semantics` (`not_exported` / `deleted_by_provider` / `device_absent` / `unknown`), and a
  declared default timezone (see time normalization below).
- `measure.coverage`: a new LOCAL deriver, `source:YYYY-MM` partitions, computing
  expected/observed/completeness/gap_reason per source per day, built on Phase 0's existing
  change-feed and worker -- no new invalidation logic.
- Time normalization, scoped down: honor an explicit timezone/offset when a source's observation
  data provides one; otherwise fall back to `source_contract`'s declared default timezone for
  that source. This replaces Phase 0's UTC-day convention for both the as-of day boundary and
  `measure.coverage`'s day buckets.

**Out (deferred to a later phase):**
- Full location-derived timezone resolution (the architecture doc's "local zone derived from
  location where known"). Phase 1 uses declared/explicit timezones only.
- Multi-family provenance modeling and the confidence-profile scoring in section 9.
- Claims, cross-source correlation, OCR/transcription, chunks, embeddings (unchanged from Phase
  0's own out-of-scope list).
- A month-grain rollup table for coverage analogous to `source_activity_7d`. Month is a
  query-side aggregation over the stored day grain unless the heatmap's real-world performance
  says otherwise.

## Migration 011 (sketch)

No canonical table is altered. New tables only:

```sql
-- declare.source_contract
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
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- measure.coverage
CREATE TABLE measure.coverage (
    source TEXT NOT NULL,
    bucket_start DATE NOT NULL,
    grain TEXT NOT NULL CHECK (grain = 'day'),
    object_kind TEXT NOT NULL,
    expected INTEGER,
    observed INTEGER NOT NULL,
    completeness NUMERIC,
    gap_reason TEXT,
    deriver_version_id BIGINT NOT NULL REFERENCES derive.deriver_version(id),
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    PRIMARY KEY (source, bucket_start, object_kind)
);
```

`observed` is populated the same way Phase 0's `source_activity_daily` counts watched kinds;
`expected` and `completeness` are NULL when `source_contract` declares no `covered_range` for
that period (mirrors Phase 0's "no row rather than a false zero" convention from
`test_run_computes_daily_and_7d_rows`), so an undeclared contract never produces a fabricated gap.

## Python runtime: `personal_data_lab/derive/`

- `derive/asof.py`: one function per canonical object kind (`as_of_events`, `as_of_records`,
  `as_of_conversations`, `as_of_assets`, `as_of_relationships`), each taking a `cutoff_run_id`
  and returning rows visible as of that cutoff with content from the matching `*_versions` row.
  Implemented as parameterized queries (not database views), since a view can't take a
  per-call cutoff parameter cleanly across all five tables without a lot of near-duplicate DDL;
  a small shared helper builds the query given a fixed, allow-listed table/versions-table pair,
  so there is exactly one WHERE-clause pattern to get right and five call sites, not five
  hand-written queries.
- `derive/coverage.py`: the new `measure_coverage` deriver, LOCAL, `source:YYYY-MM` partitions,
  reusing Phase 0's halo/partition/worker plumbing unchanged. Reads `declare.source_contract` for
  `covered_range`/`gap_semantics`/`observable_kinds`, counts observed watched-kind rows per day
  (same UTC-vs-declared-timezone day-boundary logic as the time-normalization scope-down above),
  and writes expected/observed/completeness/gap_reason.
- Time normalization lives as a small shared helper (`derive/time_norm.py`) used by both the
  as-of day-boundary logic and the coverage deriver, rather than duplicated in each: given a
  source and an observation's own tz data (if any), resolve the local day boundary.

## CLI

- `contract-declare`: writes/updates a `declare.source_contract` row (mirrors `declare-add`'s
  shape).
- `contract-show`: prints the current contract for a source (mirrors `policy-show`).
- `asof`: `personal-data-lab asof --object events --cutoff-run 1234` (or `--cutoff-at
  <timestamp>`, resolved to the latest `import_runs.id` at or before that time) -- ad-hoc
  reconstruction for debugging and for the exit test below. No new verb needed for coverage
  itself: it's just another deriver, so `derive-run`/`derive-status` already operate on it.

## Delivery as three PRs

Same shape as Phase 0:

1. **PR A (schema):** migration 011 -- `declare.source_contract` and `measure.coverage`. No
   runtime.
2. **PR B (runtime):** `derive/asof.py`, `derive/coverage.py`, `derive/time_norm.py`, the four new
   CLI verbs, and `tests/test_phase1.py` covering the exit tests below against
   `personaldata_test`.
3. **PR C (real-database rollout):** declare `source_contract` rows for the six real sources,
   run `derive sync`/`derive run` to populate `measure.coverage` against the real database,
   spot-check `asof` against real historical `import_runs.id` cutoffs, record results in
   `PROJECT_STATE.md` -- mirroring how Phase 0's exit test 10 was executed live during rollout
   rather than folded into the automated suite.

## Exit tests

1. **As-of reconstruction matches a stored snapshot (synthetic).** Insert/update/tombstone a
   record across three sequential import runs in `personaldata_test`; assert `as_of_records` at
   each of the three cutoffs matches a hand-computed expected state, including the tombstoned
   case (object absent as of the tombstoning run, present before it).
2. **Declared `not_exported` gap semantics doesn't get flagged as a gap.** A source's
   `covered_range` excludes a period entirely -> `measure.coverage` emits no row (or a
   NULL-completeness row) for that period rather than `completeness = 0`.
3. **Coverage only counts watched kinds**, matching Phase 0's `source_activity_daily` precedent,
   and a period with `observed = expected` is not gap-flagged.
4. **Backfilling a historical gap re-triggers coverage recomputation** through the existing
   change feed with no new invalidation code -- a later import touching an old month dirties that
   source's coverage partition the same way Phase 0's activity derivers already do.
5. **No canonical table is altered by migration 011** (structural assertion, mirrors Phase 0's
   own such check).
6. **(Rollout only, not automated)** `asof` reconstruction against 3 real historical
   `import_runs.id` cutoffs from the actual ingestion history, cross-checked against an
   independently written scoped query over `*_versions`/`*_observations` for the same cutoff --
   not just the function agreeing with itself.

## Decisions

Ry delegated these five calls; decided as follows, each with the reasoning that would otherwise
have been "leaning X" above:

1. **`source_contract` lives in `declare`, not `policy`.** It's a declared fact about what a
   source can observe, not an access-control rule like `policy.source_policy`/
   `policy.egress_override`.
2. **Time normalization is scoped down**: declared default timezone + honoring explicit
   per-observation offsets when a source provides them; full location-based zone resolution is
   deferred to a later phase. This is a real reduction from the architecture doc's stated
   ambition (invariant 17), traded deliberately for landing Phase 1 without also building
   location resolution.
3. **Provenance families default to one per source, no `provenance_family_id` column yet.**
   Every source today is a single independent pipeline; a column carrying a value that's always
   equal to `source` would be premature. Revisit when a real multi-family source appears (e.g.
   two independent apps feeding the same `source` value).
4. **Exit test 6 does both**: synthetic fixtures in PR B's automated suite, plus a live spot-check
   against 3 real historical `import_runs.id` cutoffs during the PR C rollout -- matching how
   Phase 0's exit test 10 was a real-database step kept separate from the automated suite.
5. **`measure.coverage` is day-grain only**, with month handled as a query-side aggregation.
   No second stored grain (no `source_activity_7d`-style rollup for coverage) until the heatmap
   query's real performance says one is needed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_018XFC7J459MYNJGRRBjKcv9
