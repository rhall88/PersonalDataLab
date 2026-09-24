# PersonalDataLab project state

Updated 2026-09-24, after Phase 6 (the semantic layer -- text units, local embeddings,
`semantic-search`) landed and ran against the real canonical database: 70,145 real text units,
70,145 real embeddings (exact 1:1, independently verified), a second real scheduler gap found and
fixed (HALO-class root dirtying), and a real concurrency race (caused by an operational mistake,
not a code defect) found, diagnosed, and corrected with zero data loss -- see "Phase 6 semantic
layer: rollout" below. Updated 2026-09-24, after Phase 5 (the claims ledger) landed and ran against the real canonical
database: schema (015, corrected same-day by 016), the `know_claim_evaluate` runtime and
`claims-list`/`claims-why` CLI, and a real scheduler ordering defect the runtime surfaced and
fixed (`derive/worker.py`'s `_claim_one` tiebreak) -- see "Phase 5 claims ledger: rollout" below.
Updated 2026-09-24, after a real review session with Ry closed Phase 2's identity queue and
Phase 4's episode candidates (built `episode-confirm`/`episode-reject` first, since it didn't
exist), and after publishing `PHASE5_IMPLEMENTATION_PLAN.md` (schema/runtime/rollout not yet
built). Updated 2026-09-24, after fixing the recurring new-deriver retroactive-dirtying gap in
`sync()` permanently (no schema change; see "New-deriver onboarding fix" below). Updated 2026-09-22, after schema version 14 added Phase 4's signals and episodes layer (baselines,
change points, anomalies, interaction dynamics/states, and cross-source detected episodes), which
ran against the real canonical database. Updated 2026-09-22, after schema version 13 added Phase 3's life ledger (per-source and composite
metrics, day-grain, and the `compare` command) and its runtime landed and ran against the real
canonical database. Updated 2026-09-22, after schema version 12 added Phase 2's identity rules
tier (COMPUTED-tier auto-linking on exact email/phone match), cross-source name-candidate queue,
and append-only identity assertion log, which ran against the real canonical database. Updated
2026-09-22, after schema version 11 added Phase 1's coverage/contract layer and its derivation
runtime (as-of reconstruction, day-grain coverage) landed and ran against the real canonical
database. Updated 2026-09-21, after schema version 10 added the derived-meaning foundation (derive/declare/
policy/measure schemas) and its Phase 0 derivation runtime landed and ran against the real
canonical database. Updated 2026-09-20, after schema version 9 added reversible identity resolution
over the canonical database. Version `0.22.0` adds five `identity-*` commands and the `identity_links`
schema;
`0.21.0` added the `search` command and `search_vector` schema; `0.20.0` recovered
doubly-escaped Nest provider rows; `0.19.0` added the `facebook_export` module; `0.18.0` qualified
Apple lossless structured ingestion and reported interpretation depth separately. PR7–PR11 were
stacked Google changes that reached `main` through PR12. Their remote feature branches remain
history, not evidence of unfinished PRs.

This file is the current implementation summary. Specifications define contracts, coverage pages
record format support, and PR reports preserve milestone evidence from the time each PR landed.
When a historical report's “next frontier” differs from this file or [`ROADMAP.md`](ROADMAP.md),
this file and the roadmap are authoritative for current sequencing.

## Canonical database baseline (2026-09-20)

One durable database — `personaldata`, on the dedicated `pdl_canonical_postgres_data` Docker
volume (never `repo_postgres_data`, which still carries pre-rebuild acceptance/dev residue) — holds
all six accepted source families at once, imported directly from the immutable raw exports under
`DataLabData\` by the current importer code, not copied from any historical acceptance database.
Schema version 7 at the time of this rebuild, later advanced to version 8 by the full-text search
migration described below. Every `import_runs` row across every source is `completed`; zero
`staging`/`finalizing`/`partial`/`failed`/`conflicted` rows exist.

| Source | Import runs | Records | Events | Assets | Relationships | Exit |
|---|---:|---:|---:|---:|---:|---:|
| Anthropic | 18 | — | 9,748 | 601 | 10,014 | 0 |
| OpenAI | 1,760 | — | 25,510 | 2,639 | — | 0 |
| iMessages | 35 | — | 71,789 | 3,136 | 75,928 | 0 |
| Apple | 897 | 476,444 | 162,318 | 380 | 161 | 0 (`production_ready: true`) |
| Google | 23,369 | 122,293 | 164,063 | 13,983 | 115,221 | 0 |
| Facebook | 2,673 | 43,051 | 91,525 | 1,786 | 1,309 | 0 (`production_ready: true`) |

Apple and Google were imported into this canonical database on 2026-09-19: Apple resumed cleanly
after an unrelated local power interruption (the 772 members already committed before the
interruption short-circuited exactly; the remaining 125 completed the accepted 897-run baseline),
and Google ran as one fresh complete pass — 25,427/25,427 data members, 51/51 registry products
present and routed, zero failed/unknown members, all 146,248,662,729 bytes accounted, in 1h19m34s.
Google's canonical counts differ from the `google_acceptance_test` snapshot recorded in
[`DATABASE_STATE.md`](DATABASE_STATE.md) by a small, explained margin (+657 records, +657
relationships, +1 event) because that snapshot predated PR13's later differential-replay fixes;
this run used the fully-fixed current importer against the canonical database directly. See
[`DATABASE_STATE.md`](DATABASE_STATE.md) for the full inventory this baseline supersedes.

This completes the canonical-rebuild sequence that document proposed: a dedicated volume, one
source at a time from raw exports only, with the old `repo_postgres_data` databases retained
un-migrated as comparison evidence rather than merged forward.

Both Apple and Google were then re-run a second time, identically, against this now-combined
canonical database to verify the exact-replay guarantee holds with multiple sources present at
once, not just per-source in isolation. Zero drift: Apple's 814/814 members and Google's
25,427/25,427 members all short-circuited, both exited 0, and every canonical count above was
unchanged before and after.

Official Facebook export ingestion (a sixth source family) was added and accepted on 2026-09-20
against an isolated acceptance database — 2,673/2,673 members, `production_ready: true`, exit 0,
zero conflicts, exact replay verified — then imported into this canonical database the same day:
2,673/2,673 members, exit 0, 574 conversations, 43,051 records, 91,525 events, 1,786 assets, and
1,309 relationships, identical to the isolated acceptance run. A second identical pass against the
canonical database short-circuited all 2,673 members with zero drift, matching the idempotency
verification already established for Apple and Google. See
[`FACEBOOK_PRODUCTION_ACCEPTANCE_REPORT.md`](FACEBOOK_PRODUCTION_ACCEPTANCE_REPORT.md).

Google's Nest semantic gap (see [`ROADMAP.md`](ROADMAP.md) and
[`GOOGLE_PRODUCT_COVERAGE.md`](GOOGLE_PRODUCT_COVERAGE.md)) was then closed and landed in this
canonical database on 2026-09-20. The fix changes decoded content for 31 rows already present from
the original import, so — consistent with the immutable-version contract that a corrected payload
never overwrites prior evidence — it minted 31 new canonical `nest_event` rows under new content
identities rather than editing the original 31 opaque ones in place. Verified directly against the
database: `nest_event` events went from 35 (31 opaque, 4 already-decoded) to 66 (the original 31
opaque rows untouched, plus 31 new rows carrying the recovered structured content); Google's total
canonical events rose from 164,032 to 164,063 (exactly +31); records, assets, and relationships were
unaffected. The full re-import processed all 25,427 data members with exit 0, zero failures,
unknowns, or conflicts. This re-import was **not** followed by a second idempotency-verification
pass (the fix is narrow, already covered by dedicated regression tests, and was verified directly
against the real corpus bytes before the code change merged) — unlike Apple, Google's original
import, and Facebook, Google's post-fix canonical state has not been re-verified for exact replay.

Apple's and Facebook's canonical counts above remain current as of their last verified idempotency
pass. Google's counts above are current as of the Nest-fix import but have not been re-verified for
exact replay since.

### Unified full-text search applied 2026-09-20

Schema version 8 (see [`QUERY_SPEC.md`](QUERY_SPEC.md)) was applied to this canonical database via
`personal-data-lab migrate`: `schema_up_to_date` went from `false` (version 7) to `true` (version
8), backfilling `search_vector` for every existing row across all four canonical tables in one
transaction. Database size grew from 7,164,343,999 to 13,048,247,999 bytes (tsvector columns plus
four GIN indexes over roughly 1.19M rows: 2,133 conversations, 524,953 events, 642,998 records,
22,525 assets).

Verified against real data with `personal-data-lab search`, not just synthetic fixtures: a search
for "meeting" correctly ranked and highlighted real Gmail messages with accurate `occurred_at`
timestamps and full provenance; a cross-kind, cross-source search for "photo" returned hits split
across `google` and `openai`; a search for "img" restricted to `--kind asset` correctly matched
real `IMG_4369`-style camera filenames — proving the `filename_search_text()` separator
normalization works on real filenames, not just the synthetic `sourdough_starter.jpg` case it was
built against. This was not followed by a second idempotency-verification pass (the migration is
purely additive — new columns/indexes/triggers — and was already tested against a disposable
database before merging); nothing about the existing six-source verification state above changes.

### Reversible identity resolution applied 2026-09-20

Schema version 9 (see [`IDENTITY_RESOLUTION_SPEC.md`](IDENTITY_RESOLUTION_SPEC.md)) was applied to
this canonical database via `personal-data-lab migrate`: `schema_up_to_date` went from `false`
(version 8) to `true` (version 9), adding the empty `identity_links` table and
`records.actor_person_id` column. Database size grew from 13,048,477,375 to 13,064,599,231 bytes —
a small, purely additive change (one new nullable column and one empty table), unlike migration 8's
data-heavy backfill.

Verified against real data, not just synthetic fixtures: `identity-candidates` surfaced 165 real
undecided identity values with correct combined events+records counts (`account_owner` with 238,269
Google events; `"Mom ❤️"` and other iMessages contacts; free-text Facebook sender names; raw Gmail
`From:` headers). A real merge linked the Facebook sender name `"Ryan Hallgarth"` (16,624 events)
and the Gmail `From:` header `"Ryan Hallgarth <rhallgarth88@gmail.com>"` (596 events) to one newly
created person, backfilling `actor_person_id` on all matching rows in both cases. A real split then
reverted the Gmail link and confirmed `actor_person_id` was nulled back out on all 596 events, and a
re-merge confirmed the reversibility contract: it inserted a **new** `identity_links` row rather
than reusing the reverted one, leaving both rows in the table as permanent history. This exercise
also surfaced and fixed a real bug: real `actor_identity` values contain non-ASCII text (the emoji
in `"Mom ❤️"`), which crashed `--format table` output with `UnicodeEncodeError` on this Windows
console's default codepage — fixed by reconfiguring CLI stdout/stderr to UTF-8 (a small follow-up
fix, not part of the identity-resolution migration itself). This was not followed by a second
idempotency-verification pass, matching migration 8's precedent (the migration is purely additive
and already tested against a disposable database before merging).

## Current source-family coverage

| Source family | Current status | Accepted evidence and boundary |
|---|---|---|
| Google Takeout | Operationally accepted in PR13; Nest semantic gap closed and landed in the canonical database 2026-09-20 | The 68-product registry has 51 present and 17 absent products in the retained 146.25 GB export. All 25,427 data members route without unknown, generic-fallback, unsupported, or raw-preserved members. Current product status is 44 fully structured, six empty, one asset-indexed, and 17 not present. Nest's 31 rows that PR13 retained as explicit opaque text (one extra layer of provider backslash-escaping) are now mechanically recovered into their real structured content and imported into the canonical database (35 → 66 `nest_event` rows; the original 31 opaque rows are retained, not overwritten). The asset-indexed Wallet surface (one content-addressed PDF) was already a correct, final disposition rather than a gap. Not re-verified for exact replay after this fix. See [`GOOGLE_PRODUCT_COVERAGE.md`](GOOGLE_PRODUCT_COVERAGE.md) and [`PR13_GOOGLE_PRODUCTION_ACCEPTANCE_REPORT.md`](PR13_GOOGLE_PRODUCTION_ACCEPTANCE_REPORT.md). |
| Official Apple privacy export | Production-ready for the retained official export | All 814 data leaves across 31 outer ZIPs and 18 nested containers persist, resume, and replay exactly with zero failures, unknowns, warnings, or conflicts. All 30 present product groups satisfy the production representation gate: 27 are explicitly losslessly structured/generic-lossless and three are fully structured/provider-specific in this corpus. Apple TV and Podcast Information and Siri were absent. Complete replay exits 0 with `production_ready: true`. See [`APPLE_PRODUCT_COVERAGE.md`](APPLE_PRODUCT_COVERAGE.md) and [`APPLE_PRODUCTION_READINESS_ACCEPTANCE_REPORT.md`](APPLE_PRODUCTION_READINESS_ACCEPTANCE_REPORT.md). |
| iMessages device export | Production-ready for the retained iMazing CSV corpus in PR15 | Two CSVs, 67,917 message rows, message/reply/reaction/edit/deletion semantics, explicit timezone input, controlled resume, and exact replay passed. The source contains attachment references but no attachment bytes. RSMF, raw iOS `sms.db`, PDF, Excel, and text exports are not claimed. See [`IMESSAGES_COVERAGE.md`](IMESSAGES_COVERAGE.md). |
| Official OpenAI export | Production-ready for the retained manifest-based export in PR17 | All 1,724 physical members, 1,187 conversations, 25,510 message events, structured JSON, retained and unavailable assets, parent edges, and message-asset edges are accounted. Controlled resume and exact replay pass with exit 0. The legacy standalone `conversations.json` path remains compatibility-only and does not provide whole-export acceptance. See [`OPENAI_INGESTION_SPEC.md`](OPENAI_INGESTION_SPEC.md) and [`PR17_OPENAI_PRODUCTION_ACCEPTANCE_REPORT.md`](PR17_OPENAI_PRODUCTION_ACCEPTANCE_REPORT.md). |
| Anthropic export | Production-ready for the retained official account export in PR19 | All six physical source files and eight JSON members are accounted. A controlled stop after the first committed conversation chunk, resume, and exact replay passed with exit 0: 269 conversations, 9,748 events, 601 explicit unavailable assets, and 10,014 relationships, with zero failures, warnings, unknowns, or conflicts. All 18 bounded units short-circuit on replay without changing run, version, or observation counts. See [`ANTHROPIC_INGESTION_SPEC.md`](ANTHROPIC_INGESTION_SPEC.md) and [`PR19_ANTHROPIC_PRODUCTION_ACCEPTANCE_REPORT.md`](PR19_ANTHROPIC_PRODUCTION_ACCEPTANCE_REPORT.md). |
| Official Facebook export | Production-ready for the retained "Download Your Information" export | All 2,673 entries across all 74 registered categories are accounted with zero unknown, generic-fallback, or unsupported members. 73 categories are `losslessly_structured`/`generic_lossless`; one (`files`) is `fully_structured`/`provider_specific`. Complete replay exits 0 with `production_ready: true`. Imported into the canonical database with zero drift on replay. See [`FACEBOOK_PRODUCT_COVERAGE.md`](FACEBOOK_PRODUCT_COVERAGE.md) and [`FACEBOOK_PRODUCTION_ACCEPTANCE_REPORT.md`](FACEBOOK_PRODUCTION_ACCEPTANCE_REPORT.md). |

## Implemented architecture

The runtime flow is:

```text
immutable provider export
    -> inventory/parser and normalized provider objects
    -> content-addressed source files plus source locations
    -> import run and run/file membership
    -> canonical conversations/events/records/assets/relationships
    -> one immutable version per distinct normalized payload
    -> narrow run/file/version observations
```

The canonical database is deliberately split from provenance:

- raw byte identity is `(source, sha256)`; a moved or repeated path adds a source location rather
  than another raw object;
- canonical logical identity is provider-scoped and namespace-scoped. Conversations, events, and
  records use `(source, source_record_namespace, source_record_id)`-style keys; assets and
  relationships use equivalent source/namespace/provider-ID keys;
- deterministic `canonical-v1` fingerprints cover normalized content, not acquisition paths,
  export timestamps, parser run order, or other provenance;
- every distinct normalized payload has one version row. Repeated identical payloads reuse that
  version, while observations record the run and source file that saw it;
- an unchanged later observation can advance the last-observed or authority watermark without
  rewriting payloads or incrementing the canonical revision;
- a newer payload replaces the canonical representation only when compatible provider authority
  evidence advances. Older payloads are retained as stale versions. Ambiguous changes are retained
  as conflicted versions while canonical content stays unchanged;
- absence from a later export is never deletion. Only an explicit provider deletion can tombstone
  a canonical object, and a later authoritative reappearance can reactivate it;
- binary content stays in immutable source files. PostgreSQL retains hashes, byte counts, media
  metadata, source-backed locators, availability state, and relationships;
- completed or conflicted run identities short-circuit exactly. Google, Apple, Facebook, iMessages,
  OpenAI, and the Anthropic v2 production path use restart-safe member or bounded-chunk
  transactions. The legacy Anthropic v1 compatibility parser retains its historical all-archive
  transaction boundary.

The database takes a PostgreSQL advisory transaction lock per `(source, snapshot_scope)` and
refuses a fingerprint-version mismatch until an explicit migration is supplied. These rules keep
local execution order, path changes, and repeated exports from becoming accidental authority.

## Schema and migrations

Fresh databases initialize directly at the latest schema version (`db/init/002_schema.sql` is kept
as a hand-maintained mirror of every migration's cumulative effect, updated alongside each new
migration file). `personal-data-lab migrate` upgrades a PR2 database idempotently through the
tracked migrations:

| Version | Purpose |
|---:|---|
| 3 | cumulative import runs, source locations, authority/lifecycle fields, and observations |
| 4 | non-null namespaced canonical identities and uniqueness constraints |
| 5 | lossless conversation/event/attachment versions and version-backed observations |
| 6 | universal durable records and external assets, each with versions and observations |
| 7 | provenance-bearing, versioned relationships |
| 8 | `search_vector tsvector` + GIN index + maintenance trigger on conversations/events/records/assets |
| 9 | `records.actor_person_id` (matching the existing `events` column) + the `identity_links` table for reversible identity merges |
| 10 | `derive`/`declare`/`policy`/`measure` schemas: the derived-meaning foundation (deriver registry,
     versioning, change feed, partition scheduling, egress policy). No canonical table altered. |
| 11 | `measure.coverage`: Phase 1's day-grain source coverage against `declare.source_contract`. |
| 12 | `identity.assertion`/`identity.not_same_as`, `identity_links.tier`/`resolution_version`: Phase 2's append-only rules-tier assertion log underneath the existing manual `identity_links`. |
| 13 | `derive.metric_def`/`derive.metric_dependency`/`measure.metric_daily`: Phase 3's life-ledger metric catalog and daily values. |
| 14 | `signal`/`episode` schemas: Phase 4's baselines, change points, anomalies, interaction dynamics/states, and declared/detected episodes. |
| 15 | `know.claim`/`know.claim_evidence`: Phase 5's claims ledger schema as first designed. No canonical table altered. |
| 16 | Corrects migration 15: drops `know.claim_evidence`, adds `know.claim.evidence` (inline JSONB) instead -- this project's deriver framework writes one output table per `compute()` call with no `RETURNING`, so a claim and a child evidence row referencing its worker-generated id can never be written together; `episode.episode.evidence` had already solved this exact problem the same way. Discovered building the runtime, not caught at schema-review time. No canonical table altered. |
| 17 | `semantic.text_unit`/`semantic.embedding_bge_small_en_v15`: Phase 6's semantic layer (`PHASE6_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md`), pgvector-backed text units and their local embeddings. Runtime and real-database rollout landed the same day -- see "Phase 6 semantic layer: rollout". No canonical table altered. |

Apple, Facebook, iMessages, OpenAI, and Anthropic v2 required no later schema migration because the
version-7 universal record, event, asset, and relationship model already expresses their data.
Migration 8 requires no importer-side changes either — every existing source becomes searchable
purely from its already-existing canonical columns. See [`QUERY_SPEC.md`](QUERY_SPEC.md). Migration
9 likewise requires no importer changes: identity links are asserted after the fact by a human, not
inferred at import time. See [`IDENTITY_RESOLUTION_SPEC.md`](IDENTITY_RESOLUTION_SPEC.md). Migration 10 also requires no
importer changes: the change feed reads only the `*_observations` rows every importer already
writes. See [`DERIVED_MEANING_ARCHITECTURE.md`](DERIVED_MEANING_ARCHITECTURE.md) and
[`PHASE0_IMPLEMENTATION_PLAN.md`](PHASE0_IMPLEMENTATION_PLAN.md).

## Phase 0 derived-meaning layer (2026-09-21)

`DERIVED_MEANING_ARCHITECTURE.md` (v2.2) was accepted as PDL's architecture of record (PR #38),
followed by `PHASE0_IMPLEMENTATION_PLAN.md` (PR #39), schema version 10 (PR #40), and the Phase 0
derivation runtime -- deriver registry, version hashing with shadow/active/retired promotion, a
change feed reading only existing `*_observations` rows, halo-aware partition scheduling, a
`SKIP LOCKED` worker, and the egress policy lattice, plus the two Phase 0 derivers
(`source_activity_daily`, `source_activity_7d`) -- in PR #41. This is explicitly single-user scope
(Ry only): `policy.egress_override` and everything downstream stays unscoped by user for now.

Migration 10 was applied to the real canonical database on 2026-09-21, after a full `pg_dump`
backup (`backups/20260921T195110Z/`). `derive sync` then `derive run --global` were run against
the complete 13GB, six-source canonical database -- the plan's exit test 10:

- `derive sync`: 15.8s, 606 dirty LOCAL partitions found across all six sources' full history.
- `derive run --global`: 4m40s wall time, 1,232 partitions processed (606
  `source_activity_daily` + 626 `source_activity_7d`, the extra 20 being halo-neighbor months),
  zero failures.
- Output: 25,369 `measure.source_activity_daily` rows, 35,154 `measure.source_activity_7d` rows.
  Per-source daily row counts: Facebook 10,005, Apple 7,412, Google 3,662, iMessages 3,174,
  OpenAI 715, Anthropic 401.
- Replay check (exit test 1): a second `derive sync` with no new imports reported
  `"is_noop": true`, dirtying zero partitions. A second `derive run --global` with nothing dirty
  returned `[]` (exit test 5).
- No canonical table was altered by the migration or by either run; `measure.*` is additive-only
  and disposable (`derive rebuild` clears and recomputes from partition_state without touching
  `derive.deriver`/`derive.deriver_version`'s idempotent registration state).

Known Phase 0 limitations, carried from the runtime PR rather than fixed here: a worker failure
between `clear_partition()` and the last replacement row leaves a partition `failed` but
partially written (nothing queries `measure.*` without checking `partition_state` yet, so this is
currently harmless); `source:undated` partitions are fingerprinted but produce zero output rows
pending Phase 1 time normalization (none were dirtied in this run -- all six sources' watched
kinds carry a usable date). Exit tests 2-4 and 6-9 are covered by `tests/test_derive.py` against
`personaldata_test`, not repeated here against the real database.

## Phase 1 derivation runtime and rollout (2026-09-22)

`PHASE1_IMPLEMENTATION_PLAN.md`'s runtime landed in PR #45 (PR B): `derive/asof.py`
(`as_of_conversations`/`as_of_events`/`as_of_records`/`as_of_assets`/`as_of_relationships`, one
shared LATERAL-join query pattern -- an object is visible as of cutoff N iff
`first_observed_run_id <= N` and it wasn't tombstoned at or before N, with content taken from the
`*_versions` row of greatest `first_observed_run_id <= N`), `derive/time_norm.py` (the one place a
source's declared `default_timezone` is resolved), and `derive/derivers/coverage.py`
(`measure_coverage`, a new root LOCAL deriver on Phase 0's unmodified worker/partition/change-feed
machinery: `observed` is the real watched-row count for a day/kind, `completeness` is 1.0 iff
`observed >= 1`, and a day outside a source's declared `covered_range` -- or an entirely
undeclared source -- gets no row at all rather than a fabricated gap). `cli.py` gained
`contract-declare`, `contract-show`, and `asof` verbs. 342 tests pass, ruff clean. Two pre-existing
test gaps surfaced (not caused) by adding a second root deriver are documented in the PR: two
`test_derive.py` exact-partition-set assertions needed `measure_coverage`'s own partition added,
and `test_migrations.py`'s `pr2_engine` fixture was dropping the shared `derive`/`declare`/
`policy`/`measure` schemas without recreating them, leaving them missing for every test file
sorting after it alphabetically.

PR C (real-database rollout) ran migration 11 against the real canonical database
(`{"schema_version": 11, "applied": [11], "changed": true}`), then declared a `source_contract`
row per source. A brand-new root deriver added after Phase 0's `derive sync` had already advanced
the shared change-feed cursor past all history gets no retroactive dirtying from `sync()` or
`rebuild()` (the former only reacts to observations after the cursor; the latter only re-dirties
partition keys already present in `partition_state`, and `measure_coverage` had none) -- a real
architectural gap the plan didn't anticipate. Worked around with a one-off script calling
`derive.scheduler.mark_dirty()` directly for every `source:YYYY-MM` key in each source's declared
`covered_range`, deliberately not resetting `change_cursor` so Phase 0's own derivers weren't
needlessly recomputed across all history. `derive run` then computed all 933 partitions, zero
failures.

The first pass at `observable_kinds` was declared from `MIN`/`MAX` timestamp existence per source
alone, which is not the same question as "does this (source, kind) actually carry a usable date on
most rows." A systematic `count(*)` vs `count(date_column)` pass per (source, kind) found real
gaps that first pass missed, corrected via re-declaration (the `contract-declare` upsert bumps
`contract_version`) followed by `derive-rebuild --deriver measure_coverage` + `derive run` to purge
the now-stale rows and recompute:

- `facebook`: `conversation` removed (0/574 rows carry `started_at`) -- was wrongly included.
- `google`: `record` removed (277/122,293, ~0.2% -- not meaningfully trackable) -- was wrongly
  included.
- `imessages`: `record` removed (4/74, ~5%) -- was wrongly included.
- `openai`: `record` removed (0/1,135) -- was wrongly included.
- Kept, but noted as partial rather than excluded, since a real (if incomplete) date signal exists
  and exclusion would throw away real coverage information: `apple.record` (163,342/476,444,
  ~34%), `google.event` (47,626/164,063, ~29%), `openai.asset` (1,039/2,639, ~39%). Their
  `completeness` numbers conflate genuine activity gaps with rows that exist but lack a date, and
  should be read with that caveat rather than as literal provider-side gaps.

Final `declare.source_contract` (all `gap_semantics=unknown`, `default_timezone=America/Chicago`):
`anthropic` [conversation, event, record, asset]; `apple` [event, record]; `facebook` [event,
record]; `google` [event]; `imessages` [conversation, event, asset]; `openai` [conversation, event,
asset]. `asset` stays excluded for apple/facebook/google (0% date coverage on all three).

Exit test 6 (live spot-check): `asof` run against three real cutoffs, each cross-checked against
an independently-written `count(*) ... WHERE first_observed_run_id <= :cutoff AND
(tombstone_run_id IS NULL OR tombstone_run_id > :cutoff)` query over the same table --
`events`/cutoff 100: 81,527 both ways; `conversations`/cutoff 55,000: 1,559 both ways;
`relationships`/cutoff 110,000: 230,235 both ways. All three matched exactly.

## Phase 4 signals + episodes: rollout (2026-09-22)

`PHASE4_IMPLEMENTATION_PLAN.md`'s signals and episodes layer landed in PR #56 (migration 014:
`signal`/`episode` schemas -- `signal.baseline`, `signal.change_point`, `signal.anomaly`,
`measure.interaction_dynamics`, `signal.interaction_state`, `episode.episode`) and PR #57
(`derive/derivers/signals.py`'s trailing-90d/lifetime baselines, PELT change-point detection, and
robust-z anomaly flagging over Phase 3's metric catalog; `interaction.py`'s per-(source,
correspondent, month) dynamics and PELT-segmented interaction states, scoped to Facebook and
iMessages 1:1 threads per decision 9; `episode.py`'s cross-source change-point clustering into
`detected` episode candidates; the `episode-declare`/`episode-list` CLI verbs). 357 tests pass,
ruff clean.

Schema version 14 was applied to the real canonical database and the derivation runtime was fully
computed against it (`derive-status` reports zero dirty and zero failed partitions across all
eleven registered derivers, including `signal_baseline`, `signal_change_point`, `signal_anomaly`,
`measure_interaction_dynamics`, `signal_interaction_state`, and `episode_detect_candidates`) --
this had already happened by the time this rollout was written up, so this section records and
verifies that real state rather than performing the rollout itself.

Rollout findings against the real data:

- **101,002 `signal.baseline` rows, 293 `signal.change_point` rows, and 1,172 `signal.anomaly`
  rows** computed across the real metric catalog. The single highest-magnitude anomaly
  (`facebook.messages_count`, 2016-01-20, observed 106, z-score ~142 against an expected band of
  roughly -1.7 to 2.7) was independently cross-checked against a direct `count(*)` over real
  `events` rows for that exact day (`source='facebook' AND event_type='message' AND
  occurred_at::date = '2016-01-20'`): **106/106 matched exactly.**
- **2,057 `measure.interaction_dynamics` rows across 365 distinct (source, correspondent) pairs**,
  scoped to Facebook and iMessages as designed. The longest-running real interaction state spans
  69 months (`imessages`, a real phone number, 2020-09 through 2026-09, mean reciprocity index
  ~0.70) and a real named Facebook correspondent (Eric Schaeffer) shows a 58-month state
  (2014-06 through 2019-09, mean reciprocity ~0.68) -- both plausible, stable long-term
  correspondences rather than artifacts.
- **9 real `detected` episode candidates**, all `status = 'candidate'` (none confirmed or rejected
  -- confirmation is a human/DECLARED-tier action per the architecture, not automated), spanning
  2016 through 2026 and clustering change points across multiple source families per episode (for
  example, episode 9 clusters 27 change points across facebook/imessages/google/openai spanning
  2026-05-02 through 2026-09-09). This is real cross-source correlation the individual providers
  never expressed on their own -- exactly the capability this phase exists to add.
- The rules-tier-adjacent identity work from Phase 2 remains at zero real auto-links
  (`identity.assertion` has 0 rows on the real database), consistent with the honest zero already
  recorded in the Phase 2 section below -- nothing about Phase 3/4 changed that.

## Phase 3 life ledger: metrics, compare, and rollout (2026-09-22)

`PHASE3_IMPLEMENTATION_PLAN.md`'s life ledger landed in PR #52 (migration 013:
`derive.metric_def`/`derive.metric_dependency`, `measure.metric_daily`) and PR #53
(`derive/derivers/metrics.py`'s 17 per-source + 3 composite metrics, riding Phase 0's existing
deriver framework unchanged; `compare.py`'s DuckDB-backed ranked change table; the
`compare` CLI verb). Scoped to communication + AI-chat metrics only: grounded directly against
the real database, only facebook/imessages/google/anthropic/openai carry any communication-shaped
activity, and there is no git/browsing/location-stay/calendar-scale source in this corpus at all
(apple has zero communication event types). 352 tests pass, ruff clean.

A real architectural gap surfaced during rollout, not during design or testing: `measure_metric_daily`
is a non-root deriver (`inputs=("measure_coverage",)`) added after Phase 0's `derive sync` had
already advanced the shared change-feed cursor past all history, so it got no retroactive
dirtying from `sync()` (only reacts to observations after the cursor) or `rebuild()` (only
re-dirties partition keys already present in `partition_state`, and this deriver had none) --
the exact same class of gap Phase 1's own rollout hit with `measure_coverage`, now recurring one
deriver layer downstream. Fixed the same way: a one-off script calling
`derive.scheduler.mark_dirty_and_propagate()` directly for every `source:YYYY-MM` key in each of
the five metric-relevant sources' declared `covered_range`, which also correctly cascaded into
dirtying `measure_metric_daily_global`'s single `GLOBAL` partition. Deliberately did not reset
`change_cursor`, so Phase 0/1's own derivers weren't needlessly recomputed. `derive run` then
computed all 735 `measure_metric_daily` partitions (zero failures), and `derive run --global`
computed `measure_metric_daily_global`'s one partition (4,725 rows written across the three
composites).

Real rollout verification:

- **37 real `measure.metric_daily` values, covering all 20 metric names at least once** (17
  per-source metrics on two real days -- 2026-08-15 and 2026-09-01 -- plus all 3 composites on
  2026-09-01), each cross-checked against an independently written `count(*)`/
  `count(distinct conversation_id)` query over the same raw `events` rows for the same day.
  **37/37 matched exactly.**
- **`compare --focal 2026-08-01:2026-09-01 --baseline 2026-07-01:2026-08-01`** (two real,
  non-overlapping full months) ranked all 20 metrics by absolute change correctly (largest first:
  `ai_chat.total_turns_count` +2,874, down to several genuinely-zero-both-months metrics at the
  bottom with a `null` percent_change rather than a division-by-zero error), and reported
  `known_days == total_days == 31` for both periods on every metric -- both months have full
  source coverage in the real data, so there was no coverage gap to surface in this particular
  comparison, but the mechanism that would surface one (verified in `test_phase3.py`'s exit test
  5) held up against the real query path end to end.
- `communication.unique_correspondents_count` reused Phase 2's `identity_normalize.classify()`
  directly (no duplicate exclusion list) and produced a real, plausible value (1,192 for August
  2026) without needing Phase 2's PRs to be merged first -- confirmed the plan's pure-Python
  import dependency, not a schema dependency, works as designed.

## Phase 2 identity resolution: rules tier and rollout (2026-09-22)

`PHASE2_IMPLEMENTATION_PLAN.md`'s rules tier landed in PR #48 (migration 012:
`identity.assertion`, `identity.not_same_as`, `identity_links.tier`/`resolution_version`) and
PR #49 (`identity_normalize.py`'s shape-sniffing, `identity_rules.py`'s COMPUTED-tier
`run_rules_tier()`, `identity_candidates.py`'s cross-source name-candidate queue, `merge_identity`/
`split_identity` writing through the new assertion log, and the `identity-rules-run`/
`identity-not-same-as`/`identity-candidates --cross-source` CLI verbs). Both build on migration
009's pre-existing manual `people`/`identity_links` tables rather than replacing them. 347 tests
pass, ruff clean.

Two real bugs were found by actually running the suite, not by inspection: migration 012's
`ALTER TABLE ADD COLUMN` wasn't idempotent, which broke `test_migrations.py`'s own teardown
restoration and, because that failure rolled back the whole teardown transaction, silently left
the shared `derive`/`declare`/`policy`/`measure`/`identity` schemas dropped for every test file
that ran afterward -- the actual cause of unrelated-looking failures in `test_phase1.py` and
`test_phase2.py`. Fixed with `ADD COLUMN IF NOT EXISTS`. Three tests also hardcoded the exact
list of migration versions `LATEST_SCHEMA_VERSION` expands to; updated for 12.

Migration 012 applied to the real canonical database (`{"schema_version": 12, "applied": [12]}`).
Rollout findings against the real data:

- **The rules tier (COMPUTED) found zero auto-linkable pairs.** `identity-rules-run --dry-run`
  returned no links and no conflicts. This is a real, honest result, not a bug: cross-checked
  directly, none of the real `actor_identity` values that carry an extractable email or E.164
  phone corroborate across two or more sources in this dataset (`google`'s email-bearing rows are
  almost entirely commercial senders plus Ry's own outgoing address; `imessages`' phone numbers
  have no email counterpart anywhere else). The rules tier's narrow, high-precision design
  (Decisions 1-2 in the plan) simply has no real-world signal to act on yet in this database --
  it isn't a placeholder that silently failed.
- **The cross-source name-candidate queue surfaced exactly the two candidates found during
  planning**: `angela barnes` and `sara slaughter`, each a `facebook`/`imessages` pair, with
  `you`/`user`/`assistant`/`account_owner` correctly excluded as role labels rather than people.
- **Left for Ry's own review, not resolved here.** Confirming `Angela Barnes` on Facebook is the
  same real person as `Angela Barnes` in iMessages is a factual claim about Ry's actual
  relationships that nothing in this dataset corroborates beyond name equality -- exactly the
  judgment call the architecture reserves for the DECLARED tier (a human), not for an
  automated pass. Run `personal-data-lab identity-candidates --cross-source` to see the current
  queue and `identity-merge <person_id> <source> <identity_value>` to act on either one, or
  `identity-not-same-as <source> <identity_value>` to reject it permanently.

## New-deriver onboarding fix (2026-09-24)

Phases 1, 3 and 4 each independently hit the same real architectural gap on rollout (see those
sections above): a deriver added to the registry after `derive sync` has already advanced the
shared change-feed cursor past all existing history gets no retroactive dirtying from `sync()`
(only reacts to observations after the cursor) or `rebuild()` (only re-dirties partition keys
already present in `partition_state`, and a brand-new deriver has none). Each time, this was
worked around with a disposable one-off script calling `derive.scheduler.mark_dirty()` or
`mark_dirty_and_propagate()` directly -- functionally correct, but something that had to be
rediscovered and rewritten by hand three times, and would have needed writing a fourth time for
Phase 5.

`sync()` now does this automatically and permanently: before reading the change feed, it detects
which derivers are getting their `derive.deriver_version` row for the very first time in this
call (genuinely new to the database, not merely a deriver whose `partition_state` happens to be
empty right now -- a distinction that matters for a `derive-rebuild` or, in tests, a truncated
table) and seeds each one's initial partition space -- from `declare.source_contract`'s declared
`covered_range` per source for a root deriver, or from whatever keys its upstream deriver(s)
already carry in `partition_state`, projected through its own `partition_class`/halo, for a
non-root deriver -- then dirties it (and cascades downstream) via the existing
`mark_dirty_and_propagate`. `derive-sync`'s CLI output now reports `backfilled_derivers` so a
rollout can see this happened without reading logs. See
[`scheduler.backfill_new_derivers`](src/personal_data_lab/derive/scheduler.py) for the full
reasoning, including why re-seeding an already-backfilled deriver via this path is intentionally
harmless (idempotent dirtying) rather than something that needs guarding against on every sync.

Covered by four new `test_derive.py` cases exercising `backfill_new_derivers` directly against
the real PostgreSQL test service (a fresh root deriver adopting a declared contract's covered
months, a non-root deriver adopting its upstream's existing partition keys under both a `LOCAL`
and a `GLOBAL` downstream projection, and a fresh chain whose upstream has no history yet
correctly seeding nothing). 361 tests pass, ruff clean, `python -m compileall` clean. The
now-permanently-superseded `seed_phase4_dirty.py` one-off script has been deleted.

## Phase 5 claims ledger: rollout (2026-09-24)

`PHASE5_IMPLEMENTATION_PLAN.md`'s claims ledger landed in three PRs: migration 015 (`know.claim`/
`know.claim_evidence` as first designed), migration 016 (a same-day correction -- see the schema
table above -- folding evidence inline as `know.claim.evidence` JSONB once building the runtime
showed the separate child table couldn't actually be populated by this project's one-output-table-
per-deriver worker), and the runtime itself: `derive/derivers/claims.py`'s `know_claim_evaluate`
(GLOBAL, `inputs=("signal_anomaly", "episode_detect_candidates")`) plus `claims-list`/`claims-why`
CLI verbs.

Building the runtime surfaced a second real, pre-existing scheduler defect, not specific to
claims: every partition a single `mark_dirty_and_propagate` call dirties shares one `dirty_since`
(Postgres's `CURRENT_TIMESTAMP` is constant for a transaction), so `derive/worker.py`'s
`ORDER BY dirty_since` alone gave no deterministic order among a LOCAL deriver and the HALO/
GLOBAL downstream it had just dirtied together -- a downstream partition could be claimed and
computed before its own upstream partition in the same batch, silently producing a stale result
nothing re-triggers within that batch. This had always been possible but only started actually
flipping observed test order once Phase 5 added more sibling fan-out into the same propagation
transaction (`test_run_computes_daily_and_7d_rows` started failing intermittently). Fixed with a
`ps.ctid` secondary sort key in `_claim_one` -- not a full scheduler redesign, but it restores
correct order for the common case (partitions freshly dirtied together), matching how
`mark_dirty_and_propagate`'s recursion always inserts a deriver's own rows before its downstream's.
See `derive/worker.py`'s own docstring for the full reasoning and its limits.

Rollout against the real canonical database: `derive-sync` correctly auto-backfilled
`know_claim_evaluate`'s initial `GLOBAL` partition via the 2026-09-24 new-deriver-onboarding fix
(the first real end-to-end proof that fix was worth landing before this phase, not just a
synthetic test of it) -- `{"know_claim_evaluate": 1}`. `derive-run --global` then computed it in
one pass: **1,181 claims** -- 1,172 `communication_change` (one per real `signal.anomaly` row,
exact match) and 9 `episode_evidence` (8 `STATISTICAL` for confirmed detected episodes, 1
`DECLARED` for the father's declared episode; the 1 remaining `candidate` episode correctly
produced no claim, per invariant 38). `derive-status` reports zero dirty and zero failed across
all 12 registered derivers. `claims-why` on the daughter's-birth claim and the grandmother's-
death claim both independently re-fetched every cited `signal.change_point`/`episode.episode` row
from its real source table with an exact match -- invariant 30's "terminates in something
concrete," verified mechanically, not just asserted.

**A real, honest limitation found during this spot-check, not fixed here:** both of those claims'
`coverage` confidence dimension came back `0.0`. This is not a bug -- `measure.coverage.
completeness` (Phase 1) is a binary per-(source, day, kind) signal ("did anything land that
day"), and `_coverage_for` takes the *minimum* across every individual change-point day an
episode's evidence cites; a multi-point episode spanning several source families very often
includes at least one day where that day's specific `object_kind='event'` completeness is
genuinely 0 (confirmed directly: `anthropic`/`google` on several of these claims' own cited dates
really do show `completeness = 0`), which drags the whole claim's coverage dimension to the
floor even when most of its evidence is well-covered. The dimension is computing exactly what it
was defined to compute; the definition itself (min-across-every-cited-day against a binary
per-day signal) makes it a weak, close-to-uninformative signal for any `episode_evidence` claim
with more than a couple of change points. Not redesigned in this PR -- a better aggregation (e.g.
scoring over the episode's overall date range rather than each literal change-point day) is real,
separable scope for a later pass, not a blocker to shipping the rest of the ledger.

367 tests passed before this rollout (371 including four new `test_phase5.py` cases covering
exit tests 1-4: `know_claim_evaluate` registration, a synthetic anomaly producing exactly the
expected `communication_change` claim, all four `episode.episode` kind/status combinations
producing the right claim/no-claim outcome, and `claims-why` independently re-fetching cited
evidence); ruff clean; `python -m compileall` clean.

## Phase 6 semantic layer: rollout (2026-09-24)

`PHASE6_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md`'s text units and local embeddings landed in three
PRs: migration 017 (`semantic.text_unit`/`semantic.embedding_bge_small_en_v15`), the runtime
(`derive/derivers/semantic.py`'s `semantic_text_unit`/`semantic_embedding_bge_small` derivers plus
the `semantic-search` CLI verb, `fastembed`/`BAAI/bge-small-en-v1.5` as a new pinned dependency),
and this rollout. 378 tests pass (371 + 7 new `test_phase6.py` cases covering exit tests 1-7),
ruff clean, compileall clean.

Building and rolling out the runtime surfaced two real findings, both fixed, neither anticipated
in the plan:

- **A second scheduler gap, this time in root-dirtying, not propagation.** Every root deriver
  before this phase was LOCAL (`source_activity_daily`, `measure_coverage`); `sync()`'s root loop
  passed raw observation-derived dirty keys straight to `mark_dirty_and_propagate` with no halo
  expansion, which was correct for all of them. `semantic_text_unit` is the first HALO-class root
  (`lookback_halo`/`lookahead_halo = "1 days"` each, so a 45-minute session near a month boundary
  is never split by partitioning alone) -- and a change in March needs to dirty semantic_text_unit's
  February partition too (a session might have started there), which the old code never did.
  Fixed in `derive/__init__.py`'s `sync()` by projecting a root's own keys through its
  `partition_class`/halo via the same `_expand_for_downstream` helper propagation already used --
  three legitimate uses of one helper now (propagation, new-deriver backfill projection, and root
  self-expansion), not three different mechanisms.
- **A real, `ctid`-tiebreak-adjacent scheduler race, caused by an operational mistake, not a code
  bug.** Two overlapping `derive-run` invocations (an artifact of a background-process handling
  mistake during rollout, not a deriver logic error) briefly processed the same partition set
  concurrently. `SKIP LOCKED` correctly prevented either from double-claiming the same partition,
  but it let `semantic_embedding_bge_small` for 3 partitions (`google:2024-03`,
  `facebook:2025-01`, `facebook:2012-11`) get claimed and correctly compute "zero text units yet"
  *before* the sibling process's `semantic_text_unit` run for those same partitions had committed
  -- exactly the GLOBAL-sibling-ordering hazard `claims.py`'s and `signals.py`'s own module
  docstrings already warn about, now observed for real, for a LOCAL/HALO pair, under genuine
  concurrency (the single-process `ctid` ordering fix from earlier in this rollout only orders
  claims *within* one process's sequential loop; it does nothing to prevent two separate processes
  racing against each other under `SKIP LOCKED`, which is a correctness property this phase
  learned it still needs, not a design flaw introduced by it). Diagnosed by joining
  `semantic.text_unit` against `semantic.embedding_bge_small_en_v15` for orphaned rows (732 found,
  concentrated in exactly those 3 partitions), fixed by re-dirtying just those partitions and
  re-running -- no data loss, no incorrect data, just a real gap between "text units exist" and
  "they got embedded" that self-corrected once identified.

Rollout against the real canonical database, fully verified, not just launched: **70,145 text
units** across facebook (9,316), google (46,972), imessages (12,514), openai (1,212), and
anthropic (131) -- google dominates because mail is one unit per message (decision 2), never
session-grouped, so its raw message count carries through almost unreduced, unlike the other four
sources' 45-minute-gap/whole-conversation grouping. **70,145 embeddings, an exact 1:1 match with
text units, verified by an explicit `LEFT JOIN ... WHERE ... IS NULL` check returning zero rows**
(not just equal counts, which the race above shows can quietly hide a real gap). `derive-status`
reports zero dirty and zero failed across all 14 registered derivers. `semantic-search` against
the real corpus: a query for "grandmother moving into assisted living" correctly surfaced real
messages from the same "Grandma's Move" thread (`conversation_id` 1758) the 2026-09-24 episode
review already identified and confirmed as episode claims 3-7 -- independent confirmation, from a
completely different derivation path, that the semantic layer is finding real, correct signal in
the corpus, not just returning syntactically valid rows.

## Verification state

- The repository currently collects 311 tests: non-integration tests plus PostgreSQL-marked
  integration tests, including the 13-test focused Facebook suite, the 6-test unified-search suite,
  and the 18-test identity-resolution suite (candidate counts summed across events and records,
  default exclusion/inclusion of already-linked values, source/min-count filtering, person
  create/list/get, merge backfilling `actor_person_id` on both events and records, merge rejecting
  an unknown person, missing evidence, or a value already linked to a different person, merge being
  idempotent and backfilling newly-imported evidence, merge annotating tombstoned rows too, split
  reverting a link while preserving history, CLI smoke tests for all five commands, and a regression
  test proving real non-ASCII identity values render correctly on a restrictive console).
- The reversible identity resolution change passes all 311 tests against the local PostgreSQL
  18/pgvector test service. Migration 9 was applied to the real canonical database on 2026-09-20
  and verified with a real merge/split/re-merge (see the canonical baseline section above), which
  also surfaced a real CLI UTF-8 output bug, fixed separately.
- The unified full-text search change passed all 293 tests against the local PostgreSQL
  18/pgvector test service. Migration 8 was applied to the real canonical database on 2026-09-20
  and verified with real queries (see the canonical baseline section above).
- The Facebook production-readiness change passed all 285 tests against the local PostgreSQL
  18/pgvector test service.
- The Apple production-readiness change passed all 250 tests against the local PostgreSQL
  18/pgvector test service, including restart/replay and changed-packaging integration coverage.
- The current Anthropic-v2 change passed all 249 tests against the local PostgreSQL 18/pgvector
  test service after migrating it to schema version 7. The guarded Anthropic test proves a
  committed 25-conversation chunk survives interruption, resume reaches the complete 30-item
  synthetic fixture, and exact replay leaves observation/version counts stable.
- Fresh Anthropic retained-corpus acceptance processed 6 source files, 8 members, 269 conversations,
  9,748 events, 601 unavailable assets, and 10,014 relationships with exit 0. A controlled stop,
  resume, and all-short-circuited replay left all run/version/observation counts unchanged.
- The latest merged PR17 acceptance evidence records all 242 tests passing against PostgreSQL
  18/pgvector, with Ruff and compilation clean.
- Source-specific real-corpus reports record Google PR13, Apple parser-v3 PR16 plus its
  production-readiness acceptance, iMessages PR15, OpenAI PR17, Anthropic PR19, and Facebook
  acceptance. Private source data, generated inventories, logs, and databases are intentionally
  not committed.

## Outstanding work in priority order

1. **(Optional, deferred by explicit instruction) Re-verify Google idempotency.** The Nest fix has
   not been re-verified for exact replay against the canonical database (see the canonical baseline
   section above). Low priority: the fix is narrow, unit-tested, and was verified directly against
   the real corpus bytes before merging.
2. **Continue past Phase 6.** Phase 6 (the semantic layer -- `semantic.text_unit`/
   `semantic.embedding_bge_small_en_v15`, `semantic-search`) is live against the real canonical
   database as of 2026-09-24: 70,145 real text units, 70,145 real embeddings (exact 1:1, verified),
   zero dirty/failed across all 14 derivers -- see "Phase 6 semantic layer: rollout" above. Next
   per `ROADMAP.md`: item 6 (OCR/transcription, explicitly deferred out of Phase 6), semantic
   *annotation*/topic-labeling (also explicitly deferred, needs a local generative model and its
   own invariant-7 discipline), and further claim-template growth (item 3). The real, un-fixed
   `coverage`-dimension weakness the Phase 5 rollout found (see above) is worth a better
   aggregation before claims lean on that dimension for anything user-facing, but isn't blocking.
3. **Continue the operational track.** Encrypted backup/restore, integrity audits, sensitive-field
   controls, incremental scheduling, performance budgets, derived-data invalidation, and corpus
   exportability remain permanent requirements.

Done 2026-09-24:
- The recurring "brand-new deriver gets no retroactive dirtying" gap that hit Phases 1, 3, and 4
  on rollout is now fixed permanently in `sync()` itself -- see "New-deriver onboarding fix" above
  -- rather than remaining a workaround to rediscover for Phase 5.
- **Phase 2's identity queue and Phase 4's episode candidates were reviewed with Ry and
  resolved**, closing both phases' own exit tests (§15: "top 200 correspondents verified" and
  "detected episodes reviewed against 10 periods you know"). Identity: both cross-source
  name-candidates (`identity-candidates --cross-source`) confirmed as real people and merged via
  `identity-merge`; the queue is now empty. Episodes: `episode-confirm`/`episode-reject` (PR #60)
  were built first, since no CLI verb existed to transition a `detected` candidate's status in
  place. 8 of the 9 detected candidates were confirmed with real, evidence-backed labels tying
  each to a specific real event Ry identified and I corroborated against the underlying message
  content (not just the change-point dates); 1 was left as an honestly-unresolved `candidate`
  rather than guessed at (invariant 38: unreviewed stays unreviewed, never optimistically
  resolved). One additional `declared` episode was added for a real event that fell in the gap
  between two detected candidates and was never caught by automatic detection at all -- a real,
  useful finding about the detector's own recall, not just a review outcome. Specific labels/notes
  (family names, dates, circumstances) are private data and intentionally not reproduced in this
  file; see `episode.episode`/`identity_links` in the canonical database directly.

Do not start MCP, an agent layer, or a second analytical source of truth before ordinary canonical
queries are fast, evidence-citing, access-controlled, and covered by the same migration and replay
discipline.
