# Phase 5 implementation plan: claims + evidence challenge

`DERIVED_MEANING_ARCHITECTURE.md` section 15's roadmap, phase 5: "`know` schema, claim templates
with confidence schemas, evidence roles, claim evaluator, `/claims/{id}/why`." Exit test: "Every
finding has a complete, finite trace; every edge is inspectable; terminal evidence is reachable
with no opaque step." This plan scopes that phase against what section 9 ("Provenance and
confidence model") actually specifies, grounded in the real canonical database as it exists after
Phases 0-4 plus the 2026-09-24 review session that confirmed 8 of Phase 4's 9 detected episodes
and merged 2 of Phase 2's identity candidates.

## Grounding (queried against the real database, 2026-09-24)

- Real counts to build claims from: `signal.anomaly` 1,172 rows, `signal.change_point` 293,
  `signal.baseline` 101,002, `measure.interaction_dynamics` 2,057 rows across 365 distinct
  `(source, correspondent)` pairs, `signal.interaction_state` 77, `episode.episode` 9 confirmed +
  1 still `candidate`, `identity_links` 6 active rows across 3 people, `derive.metric_def` 20.
  This is the entire real evidentiary base this phase has to wrap into claims -- there is no
  other statistical or semantic layer in the corpus yet (no semantic/embedding layer, per
  `DERIVED_MEANING_ARCHITECTURE.md` section 15's own note that the semantic layer runs alongside
  phases 4-6, and Phase 5 does not build it).
- **A real epistemic-tagging inconsistency, not fixed here.** `signal_change_point` and
  `signal_anomaly` are registered with `final_inference="COMPUTED"`
  (`derive/derivers/signals.py`), but the architecture's own taxonomy table (section 1) names "A
  change point in weekly volume near 2024-11-02" as its literal example of a STATISTICAL
  assertion, not COMPUTED. `measure_interaction_dynamics`/`signal_interaction_state` share the
  same COMPUTED tag despite PELT-segmenting a statistic. This was set during Phase 4 and isn't a
  Phase 5 regression, so this plan does not retroactively edit those three deriver registrations
  (`derive.deriver.final_inference` is a descriptive label read by `derive-status`, not something
  any current code branches on -- changing it now would be a no-op risk for a cosmetic fix). What
  this phase's claim evaluator does instead: assign each claim's `class` from the **claim
  template**, independent of whatever `final_inference` string its underlying deriver happens to
  carry -- exactly invariant 7 ("semantic output never masquerades as deterministic measurement")
  generalized to "a claim template's class is authoritative over its inputs' own loose tags."
- `identity_links.tier` (Phase 2, migration 012) is exactly the signal the architecture's
  `identity` confidence dimension needs: `'declared'` (a human merge, via `identity-merge`) is
  strictly stronger evidence than `'computed'` (an automated rules-tier link -- zero real rows of
  this tier exist today per Phase 2's rollout). A correspondent with no active `identity_links`
  row at all is weaker than either: a raw `(source, actor_identity)` string with no person
  resolution.
- `measure.coverage` (Phase 1) already has real per-(source, day, kind) completeness -- exactly
  what the architecture's `coverage` confidence dimension measures. No new derivation needed to
  read it.
- `episode.episode.evidence` (Phase 4) already stores its supporting `(metric, date, source)`
  triples as JSONB, keyed loosely rather than by foreign key -- this plan's `know.claim_evidence`
  follows the same precedent (a JSONB evidence reference, not a FK per possible evidence table),
  since evidence for a claim can come from `signal.anomaly`, `signal.change_point`,
  `measure.interaction_dynamics`, `signal.interaction_state`, or `episode.episode` -- five
  different tables with no common key shape.
- The 9 confirmed/candidate episodes from the 2026-09-24 review are a real, human-verified
  DECLARED/DETECTED data point this phase can build its second claim template against
  immediately, without waiting for synthetic fixtures: 7 of 9 detected episodes plus 1 declared
  episode are now `status = 'confirmed'` with real evidence-backed labels and notes; one detected
  episode remains an honestly-unresolved `candidate` (no cause identified in the data), which the
  claim evaluator must also handle -- a candidate is not evidence of anything yet, so it produces
  no claim.

## Decisions

1. **Scope: the claims ledger only, wrapping what Phases 3-4 already computed. Not the query
   template router.** `DERIVED_MEANING_ARCHITECTURE.md` section 8's eight-template catalog
   (`period_profile`, `period_change`, ...) is phases 6-7's own roadmap item ("Closed analysis
   templates", "Natural-language router") -- it needs an anchor-resolution step, a baseline-policy
   engine, and cited-prose synthesis this project has not built. Phase 5, per the phase table, is
   narrower: turn already-computed statistical/state objects into first-class, inspectable `know`
   objects with a confidence profile and a "why" trail. No new statistics are computed in this
   phase; no LLM is involved anywhere in it (matching decision 5/11 precedent from Phases 3-4 of
   deferring anything not required by the phase's own exit test).
2. **Two claim templates, both grounded in data that exists today.**
   - `communication_change`: one claim per `signal.anomaly` row on a per-source or composite
     communication/AI-chat metric (Phase 3's `METRIC_CATALOG`/`COMPOSITE_CATALOG`) -- the
     architecture's own worked example in section 9. Statement is generated from the metric's
     name, the anomaly's `observed`/`expected_low`/`expected_high`/`z_score`, and its
     `bucket_start`, e.g. "facebook.messages_count on 2016-01-20 (106) was far outside its
     expected range (roughly -1.7 to 2.7)."
   - `episode_evidence`: one claim per `episode.episode` row with `status IN ('confirmed',
     'rejected')` (never `candidate` -- invariant 38, "review queues fail unresolved, never
     optimistic"; an unreviewed candidate stays unnamed, not half-claimed). `class` is `DECLARED`
     for `kind = 'declared'` rows (a human's direct statement, per the architecture's own DECLARED
     definition) and `STATISTICAL` for `kind = 'detected'` rows confirmed by a human (the episode
     itself is a statistical inference -- change-point clustering -- that a human then verified;
     verification raises confidence, it does not change what kind of assertion the underlying
     evidence is, matching the architecture's rule that "a declaration is not 'more true' than an
     observation, it is authoritative about what you declared" -- here the human declared the
     *interpretation* of pre-existing statistical evidence, not a new independent fact). A
     `rejected` episode gets no claim at all (rejecting is a fact about the review, not a claim
     about your life).
   - Interaction-state claims (`measure.interaction_dynamics`/`signal.interaction_state`) and a
     third template for them are explicitly **deferred** to a fast-follow once the first two
     templates prove the evaluator/CLI machinery out -- real data exists (2,057 rows, 77 states)
     but scoping a good statement/confidence shape for "this relationship's character changed"
     deserves its own decision pass, not a rushed third template bolted onto this PR to hit a
     round number.
3. **Confidence: 4 of the architecture's 9 dimensions, the ones this corpus can actually
   compute.** `coverage` (from `measure.coverage`'s real completeness for the claim's source/date
   window), `statistical_strength` (the anomaly's own `z_score`, or for `episode_evidence`, the
   number of change points in its evidence), `source_independence` (count of distinct source
   families in the claim's evidence -- directly countable for `episode_evidence`, always 1 for a
   single-metric `communication_change` claim), `identity` (only meaningful, and only computed,
   for a claim whose evidence names a specific correspondent -- `identity_links.tier` for that
   `(source, actor_identity)`, or `'unresolved'` if no active link exists; **absent, not
   computed, for `communication_change` claims over aggregate metrics with no single named
   correspondent**, matching the architecture's own rule that an inapplicable dimension is absent,
   never scored). `robustness`, `anchor`, `semantic_reliability`, `temporal_precision`, and
   `spatial_precision` are deferred with grounded reasons, all following Phase 3/4's own
   established precedent of dropping what this corpus has no machinery or data for yet:
   `robustness` needs the alternate-window/threshold-perturbation machinery section 8's templates
   would build; `anchor` only applies to a `period_change`-style question with a resolved anchor
   point, which doesn't exist until phase 6; `semantic_reliability` needs the semantic/embedding
   layer (explicitly not this phase); `temporal_precision`/`spatial_precision` need a
   location/precision-tagged source this corpus does not have (same finding Phase 3 and Phase 4
   both made independently for location).
4. **Evidence roles: schema supports all four (`supports`, `contradicts`, `qualifies`,
   `contextualizes`); this phase's evaluator only ever writes `supports`.** A real `contradicts`
   search (section 8 step 10: "known counter-metrics, alternate channels, and the allowed
   alternative windows") needs a claim template to pre-declare its challenge specification --
   itself phase 6/7 machinery this project hasn't built. Writing a fake `contradicts` row now
   (e.g. "no counter-metric was found" treated as evidence of absence) would violate invariant 2
   ("unknown is never silently converted to zero"). The exit test only requires every *shown*
   edge to be inspectable and terminate concretely -- it does not require a populated
   `contradicts` set, and a claim with only `supports` evidence is still a complete, honest trace.
5. **Claim evaluator rides the existing derive framework, not new machinery.** A new GLOBAL
   deriver, `know_claim_evaluate` (`inputs=("signal_anomaly", "episode_detect_candidates")` --
   the second dependency exists purely to make the evaluator re-run whenever episode data changes,
   since `episode.py`'s own detection isn't itself a deriver output edge other derivers reference
   today; see the runtime section), clear-and-replace across all of `know.claim`/
   `know.claim_evidence` each run, exactly like Phase 4's `signal_baseline`/`episode_detect_candidates`
   pattern. This gets versioning, incremental triggering, and acyclicity (invariant 35) for free
   from machinery already proven out in Phases 0/4, rather than inventing a second evaluation
   loop. A `know.claim` row's identity is **not** stable across re-runs in this phase (clear-and-
   replace means claim ids can change run to run) -- flagged as a known limitation, not solved
   here: stable claim identity across re-derivation is real scope (the architecture's own
   "reusing standing claims where they match" from section 8 step 11), deferred until something
   actually needs to reference a claim id persistently (nothing does yet -- `claims-why` is always
   called against the current listing).
6. **CLI: `claims-list` and `claims-why`, no HTTP `/claims/{id}/why`.** This project has no HTTP
   API layer at all (every capability is a `personal-data-lab` CLI verb, per every prior phase);
   the architecture's `/claims/{id}/why` is realized here as `personal-data-lab claims-why <id>`
   printing exactly section 9's evidence-inspector shape (statement, class, template+version,
   the underlying metric/numbers, evidence rows with their roles, source independence, identity,
   and the deriver/schema version state at computation time -- not a full version vector, since
   this project has no model/embedding/prompt versioning yet to include in one). `claims-list
   [--template ...] [--class ...] [--status ...]` for browsing.

## Corrections to prior claims

- None to Phases 0-4's actual computed values. The `final_inference="COMPUTED"` tagging on
  `signal_change_point`/`signal_anomaly`/`measure_interaction_dynamics`/`signal_interaction_state`
  (grounding above) is a labeling inconsistency worth knowing about, not a computation bug --
  their output values are unaffected and this plan does not touch those four `@deriver`
  registrations.

## Schema (migration 015)

```sql
CREATE SCHEMA IF NOT EXISTS know;

CREATE TABLE know.claim (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    template TEXT NOT NULL CHECK (template IN ('communication_change', 'episode_evidence')),
    template_version TEXT NOT NULL DEFAULT '1',
    class TEXT NOT NULL CHECK (
        class IN ('OBSERVED', 'COMPUTED', 'STATISTICAL', 'SEMANTIC', 'DECLARED', 'HYPOTHESIS')
    ),
    statement TEXT NOT NULL,
    subject JSONB NOT NULL DEFAULT '{}'::jsonb,  -- e.g. {"metric": "...", "correspondent": "..."}
    confidence JSONB NOT NULL,                    -- the dimension profile, decision 3
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded')),
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE know.claim_evidence (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_id BIGINT NOT NULL REFERENCES know.claim(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('supports', 'contradicts', 'qualifies', 'contextualizes')),
    evidence_table TEXT NOT NULL,  -- 'signal.anomaly' | 'signal.change_point' | 'episode.episode'
    evidence_ref JSONB NOT NULL    -- natural-key fields identifying the row in evidence_table
);

CREATE INDEX claim_template_idx ON know.claim (template, class);
CREATE INDEX claim_evidence_claim_idx ON know.claim_evidence (claim_id);
```

`confidence` is one JSONB object per claim, keyed by dimension name (`coverage`,
`statistical_strength`, `source_independence`, `identity` -- decision 3), each value either a
number with defined empirical meaning (invariant 36) or an explicit string state
(`"declared"`/`"computed"`/`"unresolved"` for `identity`), with an absent key for a dimension
that doesn't apply -- never a null standing in for "not computed" versus "not applicable", which
would conflate two different facts. No FK from `claim_evidence.evidence_ref` to the cited tables
-- the same reasoning `episode.episode.evidence` already established: evidence rows are read at
evaluation time and cited by natural key, not database-enforced, since the cited tables are
GLOBAL-class clear-and-replace outputs whose rows don't have stable surrogate ids to reference
safely across re-runs.

## Runtime

- `derive/derivers/claims.py`: `know_claim_evaluate` (GLOBAL, `inputs=("signal_anomaly",
  "episode_detect_candidates")`, clear-and-replace all of `know.claim`/`know.claim_evidence`).
  - `communication_change`: one claim per `signal.anomaly` row, statement templated from the
    metric name/date/observed/expected range/z-score (decision 2); `statistical_strength` =
    `|z_score|`; `coverage` = that source's `measure.coverage.completeness` for the anomaly's
    `bucket_start`; `source_independence` = 1; `identity` computed only when the metric's
    `derive.metric_dependency` names exactly one source with a resolvable per-correspondent
    signal (in practice: none of Phase 3's current metrics are per-correspondent, so `identity`
    is absent for every `communication_change` claim today -- an honest, checkable starting
    state, not a bug, matching decision 3's own "absent, not computed" rule).
  - `episode_evidence`: one claim per `episode.episode` row with `status IN ('confirmed',
    'rejected')` (decision 2); `class` from `kind` per decision 2; `statement` = the episode's own
    `label` when set, else a generated summary of its date range and evidence count;
    `source_independence` = count of distinct `source` values in the episode's `evidence`
    JSONB (already present, no new computation); `statistical_strength` = number of change points
    in its evidence; `coverage` = the minimum `measure.coverage.completeness` across the episode's
    source families for its date range. `know.claim_evidence` gets one `supports` row per
    `(metric, date, source)` triple already in the episode's `evidence` JSONB, `evidence_table =
    'signal.change_point'`.
- `cli.py`: `claims-list [--template] [--class] [--status]` (prints `know.claim` rows, most recent
  first); `claims-why <id>` (the claim row, its `know.claim_evidence` rows joined back to a
  human-readable snapshot of the cited `signal.anomaly`/`signal.change_point`/`episode.episode`
  row -- read at call time, not stored redundantly -- plus the `derive.deriver_version` active
  hashes for every deriver in this claim's dependency chain, standing in for the architecture's
  full version vector until embeddings/prompts/models exist to extend it).

## Exit tests

1. `sync` registers `know_claim_evaluate` into `derive.deriver` alongside Phases 0-4's existing
   ones, with `backfill_new_derivers` (2026-09-24 fix) seeding its initial run against the real
   database's existing `signal.anomaly`/`episode.episode` history -- no one-off script needed,
   the first real end-to-end proof that fix was worth landing before this phase.
2. A synthetic `signal.anomaly` row produces exactly one `communication_change` claim whose
   `statistical_strength` matches the anomaly's own `z_score` and whose statement names the
   correct metric, date and observed/expected values.
3. A synthetic `episode.episode` row of `kind='detected', status='confirmed'` with 2 change
   points across 2 source families produces one `episode_evidence` claim with `class='STATISTICAL'`,
   `source_independence=2`, and exactly 2 `know.claim_evidence` rows; a `kind='declared'` row
   produces `class='DECLARED'`; a `status='candidate'` row produces no claim at all; a
   `status='rejected'` row produces one claim (evidence rejection is itself a fact worth an
   inspectable trace, distinct from silently deleting the episode).
4. `claims-why` on a real claim id reproduces every field in section 9's evidence-inspector shape
   that this phase implements (statement, class, template+version, cited evidence rows with
   roles, confidence profile, deriver-version state) and every cited evidence row is independently
   re-fetchable from its source table by the `evidence_ref` given -- invariant 30's "terminates
   in something concrete," checked mechanically, not asserted.
5. Real-database rollout: `know_claim_evaluate` run against the real canonical database produces
   one `episode_evidence` claim for each of the 8 real confirmed episodes from the 2026-09-24
   review session (7 detected-and-confirmed plus 1 declared) and roughly 1,172
   `communication_change` claims (one per real `signal.anomaly` row); `claims-why` on the
   grandmother's-death episode claim (id 7) and the daughter's-birth claim (id 1) both walk back
   to real, independently-verifiable `signal.change_point`/`episode.episode` rows with no opaque
   step.
