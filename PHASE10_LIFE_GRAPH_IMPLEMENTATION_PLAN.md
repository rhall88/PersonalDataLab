# Phase 10 implementation plan: the life graph (one consolidated generative pass + downstream synthesis)

**Revision history (both same day, 2026-09-26):**
- **v2** fixed: a missing relationship layer (v1 produced nodes, no edges), an internal
  contradiction (contradiction detection promised a resolved/unresolved distinction the schema
  couldn't represent), an entity-identity bug (`entity_link` uniquely indexed on bare surface
  text), and two places state was flattened to a boolean.
- **v3 (this revision)** fixed four more, found by the same kind of close read: **person mentions
  could never resolve** (entity.entity explicitly excludes people, but `entity_link` only points at
  entity.entity — every `Person → X` edge, arguably the most valuable kind, was structurally
  impossible); **the entity resolver's own "propose, don't auto-apply" claim wasn't true of its
  schema** (it wrote straight to the active-link table with no candidate/confirmed/rejected state);
  **decision/open-loop cross-occurrence identity didn't exist** (state transitions were promised —
  "opened → continued → resolved" — with no mechanism clustering which occurrences are the same
  thread); and **`generative.extraction` was going to get mutated by downstream code**, contradicting
  its own "immutable model output" premise. Also resolved the one flagged open TODO
  (`event_resolve_candidates`' two-relation problem) explicitly rather than leaving it for
  implementation to improvise.

What's survived three passes unchanged: the one-pass design, ordinal salience, `people` untouched,
deferred episode summarization/motifs, and the core decisions→schema→runtime→exit-test structure.

Ry's own framing, verbatim intent preserved: move from `message → embedding + topic` toward
`raw artifact → entities → events → relationships → claims → episodes → eras`, so the eventual UI
can move fluidly between projections of one underlying dataset — exactly `COCKPIT_VISION.md`'s
"question-driven visual canvas that can fluidly transform." This plan scopes the backend half of
that only: schema and derivers, per the standing instruction to stop before UI work.

## Grounding (queried against the real canonical database, 2026-09-26)

| Table | Real count today |
|---|---|
| `semantic.text_unit` | 70,145 (5 distinct sources) |
| `episode.episode` | 10 |
| `know.claim` | 1,189 |
| `identity_links` (active) | 6, resolving 3 real people |
| `people` | 3 rows — `(id, display_name, created_at)`, no `kind` column, built person-only |

`people`/`identity_links` stay untouched (decision 3); `derive/worker.py`'s `_write_rows` enforces
exactly one `output_relation` per deriver (migration 016's own precedent) — every schema decision
below, including the new candidate-table pattern in decisions 12/13/15, respects that constraint
explicitly rather than discovering it during implementation.

## Decisions

1. **One deriver reads raw text once: `generative_extract_text_unit`.** LOCAL, 1:1 with
   `semantic.text_unit`, `final_inference = "SEMANTIC"`, `inputs=("semantic_text_unit",)`. Emits one
   structured JSON envelope per unit (below). Everything downstream is pure logic over this table's
   output plus other already-existing derived tables — nothing calls the model on raw text again.
2. **Same model as Phase 7, reused not replaced.** Phi-3.5-mini-instruct Q4_K_M,
   `llama-cpp-python`, grammar-constrained, `model_ref`-hashed, same `PDL_ANNOTATION_MODEL_PATH`/
   `PDL_ANNOTATION_N_GPU_LAYERS`/rented-GPU pattern this session is proving out for Phase 7.
3. **Non-person entities get a new, additive `entity` schema — `people`/`identity_links` untouched.**
   `entity.entity` (`kind` IN `('organization','place','project','named_concept')`,
   `canonical_name`) is never auto-created by a deriver, same discipline `people` already has
   (`identity-create-person` is a human action).
4. **Mentions are occurrences; resolutions are separate — for both people and non-person
   entities.** `entity.mention` is one row per *occurrence* (`text_unit_id`, `slot_index`,
   `surface_text`, `kind`, and — hardening — `computed_epoch_id` for lineage back to the extraction
   run that produced it), written by a pure explode-only deriver. Resolving a mention to *what it
   actually refers to* is a separate step (decisions 5 and 15) — never assumed 1:1 with its surface
   string, which can't be true ("Liberty" is Liberty Mutual in one unit and could be anything in
   another).
5. **Person mentions resolve too — through a parallel table and a unifying view, not through
   `entity.entity`.** The v2 bug: `entity.mention.kind` allowed `'person'`, but `entity.entity`
   explicitly excludes people and `entity_link` only references `entity.entity` — so a mention of
   "Ryan" or "Sarah" had nowhere to resolve, and every `Person → Project`/`Person → Place`/
   `Person → Event` edge (some of the most valuable ones this whole layer exists to produce) was
   structurally unreachable. Fixed: `entity.person_link` mirrors `entity.entity_link` exactly but
   targets `people(id)` instead — `people` stays untouched, this is purely additive. A
   `entity.resolved_mention` view `UNION ALL`s both (`resolved_kind` = `'person'` or `'entity'`,
   `resolved_id`), and `entity.relation_edge` (decision 8) joins through that unified view on both
   sides, so a relation between a person-mention and a place-mention resolves cleanly without either
   side caring which table the other side happened to land in.
6. **Salience is a 3-value ordinal (`low`/`medium`/`high`), unchanged since v1.**
7. **Interaction type allows up to two labels, unchanged since v2** — `interaction_primary` +
   `interaction_secondary`, same closed 11-value taxonomy. Hardening added this pass:
   `interaction_secondary`, if set, must differ from `interaction_primary` — a DB-level `CHECK`, not
   just a prompting hope, since "conflict + conflict" carries no information a single label didn't
   already give.
8. **Relation candidates, unchanged core design since v2** — up to *N* (decision 9)
   `{subject_slot, relation_type, object_slot, evidence_mode}` tuples per unit, closed 12-value
   taxonomy (`created`, `uses`, `works_at`, `member_of`, `owns`, `located_at`, `participated_in`,
   `about`, `supports`, `opposes`, `part_of`, `influenced`), deliberately no `related_to` junk-drawer
   value. Three hardening rules added this pass, all enforced in `compute()` before a row is ever
   written (a grammar can constrain *shape*, not "does this reference a slot that actually exists in
   this response," which is data-dependent):
   - `subject_slot != object_slot` (DB `CHECK`, belt-and-suspenders with the application check).
   - Both slots must index real positions in that same response's `entities[]` array — a
     model-generated out-of-range reference is dropped, not stored, and counted so a real defect
     rate is visible rather than silently absorbed.
   - The three most interpretation-heavy relation types — `influenced`, `supports`, `opposes` —
     require `evidence_mode = 'explicit'` to be stored at all. An *implied* causal or stance claim
     is exactly the kind of overreach that erodes trust in the whole layer; the other nine relation
     types carry no such restriction since they're typically more directly stated in text anyway
     (`works_at`, `owns`, etc.).
   - Entity-kind plausibility (`works_at` typically wants person→organization, etc.) is *flagged*
     for review via a documented compatibility table, not hard-filtered — an implausible combination
     is more likely to be a real, interesting exception than a reason to silently discard data this
     phase exists to surface.
9. **Neither the entity slot count nor the relation slot count is fixed by this plan.** Before the
   grammar is finalized: run the **actual Phi extraction prompt** (not a cheap heuristic proxy —
   the review that caught this specifically flagged a capitalization-heuristic estimate as
   insufficient, since the slot count becomes a hard information-loss ceiling once locked) against a
   **stratified sample** — a few hundred `semantic.text_unit` rows spanning all 5 sources,
   short/medium/long lengths, and multiple time periods, not just whatever's convenient — and
   measure the real distribution of mentions and relations per unit before choosing the bound.
10. **Events get role slots and a raw time expression; the model never does date math.**
    `event_type`, `event_summary`, `event_actor_slot`/`event_object_slot`/`event_location_slot`
    (indices into `entities[]`, or null), and `event_time_expression` — the literal text ("yesterday,"
    "last May"), never normalized by the model. Closed 9-value taxonomy: `purchase`,
    `employment_change`, `relationship_change`, `relocation`, `health_event`, `legal_event`,
    `milestone`, `other`, `none`.
11. **The resolved date range is NOT written back into `generative.extraction` — it lives in its own
    table.** The v2 bug: treating a SEMANTIC table as a place downstream deterministic code could
    also write into blurs "what the model said" with "what our code derived from it." Fixed:
    `event.time_resolution` (`text_unit_id`, `raw_expression`, `resolved_range`, `resolution_rule`,
    `computed_epoch_id`) is its own table, written by its own small deriver
    (`event_resolve_time_expressions`, LOCAL, `final_inference="COMPUTED"`). A small deterministic
    rule table (yesterday/today/tomorrow ± n days; "last/next \<weekday\>"; "last week/month/year" as
    bucketed ranges; explicit "\<Month\> \<Year\>"/"\<Year\>" parsed directly; anything unparseable
    leaves `resolved_range NULL` and `resolution_rule = 'unparsed'`) resolves the raw expression
    against `semantic.text_unit.started_at` — auditable code, never a second model call.
    `generative.extraction` itself is never altered after it's written.
12. **Event resolution is a two-stage candidate/confirm pattern, resolving the one open design
    question v2 left unsettled.** `derive/worker.py` permits exactly one `output_relation` per
    deriver — so `event_resolve_candidates` (GLOBAL, `final_inference="COMPUTED"`, `inputs=
    ("generative_extract_text_unit", "entity_resolve_mentions", "event_resolve_time_expressions",
    "semantic_embedding_bge_small")`) writes *only* `event.cluster_candidate` (`event_type`,
    `proposed_label`, `occurred_range`, `member_text_unit_ids` JSONB, `evidence` JSONB, `status` IN
    `('candidate','confirmed','rejected')`) — clustering signals: event-type match, resolved-range
    overlap, resolved-entity overlap (via `entity.resolved_mention`), embedding similarity, source
    independence. A human-invoked `event-confirm <candidate_id>` CLI verb then does the
    multi-table write transactionally: creates `event.canonical_event` plus however many
    `event.event_link` rows the candidate's members need. The deriver proposes; the CLI, not a
    second deriver, performs the actual multi-relation write — the review boundary made explicit
    rather than implicit. `event-reject` marks a candidate `rejected` without creating anything.
    This directly fixes the review's point about cross-source stitching being a real, distinct
    mechanism from episode detection (episode detection stays the coarser statistical-change-point
    clustering it already is; this is the finer-grained, specific-event version of the same idea).
13. **Decisions and open loops get cross-occurrence identity too — the same real gap event
    resolution already had, just not yet fixed for this side.** The v2 schema let a single
    occurrence carry `decision_state`/`open_loop_state`, and claimed downstream claim templates
    could show a loop's "full state history" — but nothing clustered *which occurrences are the same
    thread*, so that history couldn't actually exist. Fixed, mirroring decision 12's exact shape:
    `intent_resolve_candidates` (GLOBAL, `final_inference="COMPUTED"`, `inputs=
    ("generative_extract_text_unit", "entity_resolve_mentions", "semantic_embedding_bge_small")`)
    clusters decision/open-loop candidates using embedding similarity, resolved-entity overlap,
    chronological order, and state-transition plausibility (a real, human-reviewable signal, not a
    hard rule — `opened → continued → resolved/abandoned` and `considering → decided → implemented
    → reversed` are the expected shapes, a violation is a reason to flag for review, not to refuse
    clustering). Writes only `intent.cluster_candidate` (`kind` IN `('decision','open_loop')`,
    `proposed_label`, `member_text_unit_ids`, `evidence`, `status`). `intent-confirm`/`intent-reject`
    CLI verbs create `intent.thread` + `intent.thread_link` transactionally, same pattern as
    `event-confirm`.
14. **Entity/person mention resolution is also a candidate/confirm pattern — not a direct write,
    despite what v2's prose claimed.** The v2 bug: `entity_resolve_mentions`'s own description said
    it "proposes candidates... never auto-applies," but its declared output was the active-link
    table directly, with no candidate/confirmed/rejected state to actually stage a proposal in.
    Fixed: `entity_resolve_mentions` (GLOBAL, `final_inference="COMPUTED"`, `inputs=
    ("entity_mention_extract",)`) writes only `entity.link_candidate` — `mention_id`, exactly one of
    `candidate_person_id`/`candidate_entity_id` (a `CHECK (num_nonnulls(...) = 1)`, not a polymorphic
    reference needing a trigger to stay honest), `resolution_tier` IN `('exact_match','fuzzy_match',
    'manual')`, `evidence`, `status`. `entity-confirm <candidate_id>` writes the real
    `entity.entity_link` or `entity.person_link` row (whichever target is set) and marks the
    candidate `confirmed`; `entity-reject` marks it `rejected` without writing anything.
    `entity-merge <mention_id> <person_id-or-entity_id>` stays available as the direct
    human-asserted path (`resolution_tier = 'manual'`) for a link the algorithm never proposed at
    all — matching `identity-merge`'s own existing direct-write UX rather than forcing a human's own
    direct assertion through an unnecessary candidate stage.
15. **Three new `know.claim` templates, now over resolved (not raw per-occurrence) data.**
    `event_claim` (one per confirmed `event.canonical_event`), `decision_claim`/`open_loop_claim`
    (one per confirmed `intent.thread`, carrying its member occurrences' full state sequence — which
    only exists now because decision 13 gives it somewhere to come from). No new inference —
    packaging already-resolved rows, same shape `episode_evidence`/`relationship_state_shift`
    established.
16. **Claims contradiction detection stays scoped to what pure logic can actually verify.**
    `claim_contradiction_detect` (GLOBAL, `final_inference="HYPOTHESIS"`) checks only: (a) two
    `communication_change`/`relationship_state_shift` claims about the same subject with opposite
    directional statements over overlapping ranges, and (b) two `open_loop_claim` rows for the *same
    resolved `intent.thread`* — now a real, checkable identity, not an assumption — where the most
    recent member state is `resolved` in one derivation path and anything else in another (only
    possible for a thread with genuinely conflicting source evidence). No free-text semantic
    contradiction detection is attempted or claimed.
17. **Episode/era summarization and multi-resolution rollups stay deferred, unchanged since v1.**
18. **Temporal motif detection stays deferred, unchanged since v1.**

## The single-pass JSON envelope (Phi's own output — unchanged in shape since v2)

```json
{
  "interaction_primary": "<enum-or-none>",
  "interaction_secondary": "<enum-or-none, must differ from primary>",
  "salience": "low|medium|high",
  "entities": [
    {"mention": "<string>", "kind": "person|organization|place|project|named_concept|none"}
  ],
  "relations": [
    {"subject_slot": 0, "relation_type": "<enum>", "object_slot": 1, "evidence_mode": "explicit|implied"}
  ],
  "event": {
    "type": "<enum-or-none>",
    "summary": "<string>",
    "actor_slot": null,
    "object_slot": null,
    "location_slot": null,
    "time_expression": "<literal text from the unit, e.g. 'yesterday', or empty>"
  },
  "decision": {"state": "<enum>", "summary": "<string>"},
  "open_loop": {"state": "<enum>", "summary": "<string>"}
}
```

Array lengths pending decision 9's benchmark. This envelope is the *only* thing Phi produces and
the *only* thing that lands in `generative.extraction` — every resolution, clustering, and
normalization step described above operates on copies/derivations of it in other tables, never by
editing it in place (decision 11).

## Schema (migration number: whichever lands after Phase 9's 022)

```sql
CREATE SCHEMA IF NOT EXISTS generative;

CREATE TABLE generative.extraction (
    text_unit_id BIGINT PRIMARY KEY REFERENCES semantic.text_unit(id) ON DELETE CASCADE,
    interaction_primary TEXT CHECK (interaction_primary IN (
        'support', 'conflict', 'planning', 'affection', 'logistics', 'financial',
        'creative_collaboration', 'advice', 'argument', 'reconciliation'
    )),
    interaction_secondary TEXT CHECK (interaction_secondary IN (
        'support', 'conflict', 'planning', 'affection', 'logistics', 'financial',
        'creative_collaboration', 'advice', 'argument', 'reconciliation'
    )),
    salience TEXT NOT NULL CHECK (salience IN ('low', 'medium', 'high')),
    entity_mentions JSONB NOT NULL DEFAULT '[]'::jsonb,
    relation_candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
    event_type TEXT CHECK (event_type IN (
        'purchase', 'employment_change', 'relationship_change', 'relocation',
        'health_event', 'legal_event', 'milestone', 'other'
    )),
    event_summary TEXT,
    event_actor_slot INTEGER,
    event_object_slot INTEGER,
    event_location_slot INTEGER,
    event_time_expression TEXT,  -- raw only; NO resolved-range column here (decision 11)
    decision_state TEXT NOT NULL DEFAULT 'none' CHECK (decision_state IN (
        'none', 'considering', 'decided', 'implemented', 'abandoned', 'reversed'
    )),
    decision_summary TEXT,
    open_loop_state TEXT NOT NULL DEFAULT 'none' CHECK (open_loop_state IN (
        'none', 'opened', 'continued', 'resolved', 'abandoned'
    )),
    open_loop_summary TEXT,
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (interaction_secondary IS NULL OR interaction_secondary != interaction_primary)
);

CREATE SCHEMA IF NOT EXISTS entity;

-- Never written by a deriver -- created only via `entity-create`, exactly how `people` rows are
-- only ever created via `identity-create-person`.
CREATE TABLE entity.entity (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('organization', 'place', 'project', 'named_concept')),
    canonical_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- One row per mention OCCURRENCE (decision 4) -- explode-only, no resolution here.
CREATE TABLE entity.mention (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    text_unit_id BIGINT NOT NULL REFERENCES semantic.text_unit(id) ON DELETE CASCADE,
    slot_index INTEGER NOT NULL,
    surface_text TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('person', 'organization', 'place', 'project', 'named_concept')),
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (text_unit_id, slot_index)
);

-- Proposed resolutions -- candidate/confirmed/rejected, never a direct active write (decision 14).
-- Exactly one of the two candidate-target columns is set; a real CHECK, not app-level trust.
CREATE TABLE entity.link_candidate (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mention_id BIGINT NOT NULL REFERENCES entity.mention(id) ON DELETE CASCADE,
    candidate_person_id BIGINT REFERENCES people(id) ON DELETE CASCADE,
    candidate_entity_id BIGINT REFERENCES entity.entity(id) ON DELETE CASCADE,
    resolution_tier TEXT NOT NULL CHECK (resolution_tier IN ('exact_match', 'fuzzy_match', 'manual')),
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'confirmed', 'rejected')),
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (num_nonnulls(candidate_person_id, candidate_entity_id) = 1)
);

-- The two real, active resolution tables -- written only by entity-confirm/entity-merge (CLI),
-- never by a deriver. Twins by design (decision 5): same reversible shape, different target.
CREATE TABLE entity.entity_link (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mention_id BIGINT NOT NULL REFERENCES entity.mention(id) ON DELETE CASCADE,
    entity_id BIGINT NOT NULL REFERENCES entity.entity(id) ON DELETE CASCADE,
    lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK (lifecycle_state IN ('active', 'reverted')),
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reverted_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX entity_link_active_idx ON entity.entity_link (mention_id) WHERE lifecycle_state = 'active';

CREATE TABLE entity.person_link (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mention_id BIGINT NOT NULL REFERENCES entity.mention(id) ON DELETE CASCADE,
    person_id BIGINT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK (lifecycle_state IN ('active', 'reverted')),
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reverted_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX person_link_active_idx ON entity.person_link (mention_id) WHERE lifecycle_state = 'active';

-- Unifies both resolution kinds so relation edges never have to care which table a given mention
-- happened to resolve into (decision 5's fix for the person-mention hole).
CREATE VIEW entity.resolved_mention AS
    SELECT mention_id, 'person'::text AS resolved_kind, person_id AS resolved_id
    FROM entity.person_link WHERE lifecycle_state = 'active'
    UNION ALL
    SELECT mention_id, 'entity'::text AS resolved_kind, entity_id AS resolved_id
    FROM entity.entity_link WHERE lifecycle_state = 'active';

-- Exploded relation candidates -- still slot-indexed, not yet resolved (decision 8).
CREATE TABLE entity.relation_candidate (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    text_unit_id BIGINT NOT NULL REFERENCES semantic.text_unit(id) ON DELETE CASCADE,
    subject_slot INTEGER NOT NULL,
    relation_type TEXT NOT NULL CHECK (relation_type IN (
        'created', 'uses', 'works_at', 'member_of', 'owns', 'located_at',
        'participated_in', 'about', 'supports', 'opposes', 'part_of', 'influenced'
    )),
    object_slot INTEGER NOT NULL,
    evidence_mode TEXT NOT NULL CHECK (evidence_mode IN ('explicit', 'implied')),
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (subject_slot != object_slot)
);

CREATE VIEW entity.relation_edge AS
    SELECT
        rc.id AS relation_candidate_id,
        subj.resolved_kind AS subject_kind, subj.resolved_id AS subject_id,
        rc.relation_type,
        obj.resolved_kind AS object_kind, obj.resolved_id AS object_id,
        rc.evidence_mode, rc.text_unit_id
    FROM entity.relation_candidate rc
    JOIN entity.mention subj_m ON subj_m.text_unit_id = rc.text_unit_id AND subj_m.slot_index = rc.subject_slot
    JOIN entity.resolved_mention subj ON subj.mention_id = subj_m.id
    JOIN entity.mention obj_m ON obj_m.text_unit_id = rc.text_unit_id AND obj_m.slot_index = rc.object_slot
    JOIN entity.resolved_mention obj ON obj.mention_id = obj_m.id;

CREATE SCHEMA IF NOT EXISTS event;

-- Deterministic-code output, kept OUT of generative.extraction (decision 11).
CREATE TABLE event.time_resolution (
    text_unit_id BIGINT PRIMARY KEY REFERENCES semantic.text_unit(id) ON DELETE CASCADE,
    raw_expression TEXT NOT NULL,
    resolved_range DATERANGE,
    resolution_rule TEXT NOT NULL,  -- e.g. 'relative_day', 'named_month_year', 'unparsed'
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Proposed only -- event-confirm/event-reject (CLI) does the real multi-table write (decision 12).
CREATE TABLE event.cluster_candidate (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type TEXT NOT NULL,
    proposed_label TEXT NOT NULL,
    occurred_range DATERANGE,
    member_text_unit_ids JSONB NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'confirmed', 'rejected')),
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE event.canonical_event (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type TEXT NOT NULL,
    label TEXT NOT NULL,
    occurred_range DATERANGE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE event.event_link (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_event_id BIGINT NOT NULL REFERENCES event.canonical_event(id) ON DELETE CASCADE,
    text_unit_id BIGINT NOT NULL REFERENCES semantic.text_unit(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE SCHEMA IF NOT EXISTS intent;

-- Proposed only -- intent-confirm/intent-reject (CLI) does the real write (decision 13).
CREATE TABLE intent.cluster_candidate (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('decision', 'open_loop')),
    proposed_label TEXT NOT NULL,
    member_text_unit_ids JSONB NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'confirmed', 'rejected')),
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE intent.thread (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('decision', 'open_loop')),
    label TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE intent.thread_link (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread_id BIGINT NOT NULL REFERENCES intent.thread(id) ON DELETE CASCADE,
    text_unit_id BIGINT NOT NULL REFERENCES semantic.text_unit(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE know.claim_contradiction (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_id_a BIGINT NOT NULL REFERENCES know.claim(id) ON DELETE CASCADE,
    claim_id_b BIGINT NOT NULL REFERENCES know.claim(id) ON DELETE CASCADE,
    note TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'confirmed', 'rejected')),
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (claim_id_a < claim_id_b)
);

ALTER TABLE know.claim DROP CONSTRAINT claim_template_check;
ALTER TABLE know.claim ADD CONSTRAINT claim_template_check
    CHECK (template IN (
        'communication_change', 'episode_evidence', 'relationship_state_shift',
        'event_claim', 'decision_claim', 'open_loop_claim'
    ));
```

## Runtime

- `derive/derivers/generative.py`: the single pass (`generative_extract_text_unit`). Hand-authored
  GBNF grammar for the full envelope; `_MAX_TOKENS` grounded post-benchmark (decision 9); reuses
  `annotation.py`'s `_get_model()`/`_bounded_prompt()` verbatim, including its context-overflow fix
  found live this session. `compute()` also runs the slot-validity/evidence-mode filters from
  decision 8 before writing `generative.extraction` rows.
- `derive/derivers/entity.py`:
  - `entity_mention_extract` (LOCAL, `COMPUTED`, `output_relation="entity.mention"`) — explode only.
  - `relation_candidate_extract` (LOCAL, `COMPUTED`, `output_relation="entity.relation_candidate"`) —
    twin shape, explode plus the decision-8 validity filters.
  - `entity_resolve_mentions` (GLOBAL, `COMPUTED`, `output_relation="entity.link_candidate"`) —
    proposes only; never writes an active link.
- `derive/derivers/event.py`:
  - `event_resolve_time_expressions` (LOCAL, `COMPUTED`, `output_relation="event.time_resolution"`) —
    the deterministic rule table from decision 11.
  - `event_resolve_candidates` (GLOBAL, `COMPUTED`, `output_relation="event.cluster_candidate"`) —
    proposes only.
- `derive/derivers/intent.py`:
  - `intent_resolve_candidates` (GLOBAL, `COMPUTED`, `output_relation="intent.cluster_candidate"`) —
    proposes only.
- `derive/derivers/claims.py` (extended): `_event_claims`/`_decision_claims`/`_open_loop_claims`
  folded into `know_claim_evaluate`; `claim_contradiction_detect` (GLOBAL, `HYPOTHESIS`), scope per
  decision 16.
- CLI, all following the identity/episode review-queue precedent exactly:
  - `entity-create`, `entity-candidates`, `entity-confirm`, `entity-reject`, `entity-merge`,
    `entity-split`.
  - `event-candidates`, `event-confirm`, `event-reject`.
  - `intent-candidates`, `intent-confirm`, `intent-reject`.
  - `claim-contradictions-list`, `claim-contradiction-confirm`, `claim-contradiction-reject`.
- `pyproject.toml`: no new dependency for the pass itself; entity mention clustering starts with
  plain string normalization, a fuzzy-match library only if decision 9's benchmarking shows it's
  needed against real mention data.

## Exit tests

1. `sync` registers every new deriver with the right `final_inference` (`SEMANTIC` for the pass;
   `COMPUTED` for every explode/resolve/candidate deriver; `HYPOTHESIS` for contradiction
   detection).
2. The grammar always produces valid JSON matching the envelope across a varied synthetic batch,
   including relation tuples whose slots are always valid positions in that response's own
   `entities[]`.
3. A synthetic unit with an out-of-range or self-referencing relation slot has that specific tuple
   dropped (not the whole row failed) — proving decision 8's defensive validation is real, not
   assumed.
4. A synthetic `influenced` relation with `evidence_mode='implied'` is dropped; the same relation
   with `evidence_mode='explicit'` is kept.
5. A mention of a person's name resolves through `entity.resolved_mention` with
   `resolved_kind='person'`, and a relation between two person-mentions produces a real
   `entity.relation_edge` row — the exact case v3 fixes, verified directly, not just asserted fixed.
6. `entity_resolve_mentions` writes only `entity.link_candidate` rows; no `entity.entity_link` or
   `entity.person_link` row exists until `entity-confirm` is actually invoked.
7. `event_resolve_candidates` clusters three synthetic same-topic, same-timeframe, cross-source
   event candidates into one `event.cluster_candidate`; `event-confirm` on it creates exactly one
   `event.canonical_event` and the right number of `event.event_link` rows in one transaction.
8. `intent_resolve_candidates` clusters three synthetic occurrences of the same open loop
   (`opened` → `continued` → `resolved`) into one `intent.cluster_candidate`; after
   `intent-confirm`, `open_loop_claim` for that thread carries all three states in order.
9. `claim_contradiction_detect` finds a contradiction only when two `open_loop_claim` rows share the
   *same confirmed `intent.thread`* and disagree on latest state — not for two superficially similar
   but structurally unrelated loops.
10. `event_resolve_time_expressions("yesterday", anchor)` resolves to `[anchor - 1 day, anchor)`
    with `resolution_rule='relative_day'`; an unparseable expression gets `resolved_range NULL`,
    `resolution_rule='unparsed'` — never a guessed date.
11. `clear_partition` isolation: clearing one source/month of `generative_extract_text_unit` never
    touches another partition's `generative.extraction`, `entity.mention`, or
    `entity.relation_candidate` rows.

## Sequencing

Implementation starts once Phase 7's annotation rollout is real and complete — same infrastructure,
second use. Before the grammar is finalized, decision 9's benchmark runs the actual Phi prompt
against a real stratified sample to set slot counts from measured data. Real-database rollout is
gated on the one GPU pass running against all 70,145 units; everything from decision 4 onward is
pure logic (explode, resolve-candidates, confirm) over data that pass already produced, so only one
rented-hardware decision covers this entire phase.
