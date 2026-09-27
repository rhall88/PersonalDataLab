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

**Fixed same day, in a follow-up commit (before this file's next section was written):** added
`_coverage_for_range`, used only by `episode_evidence` -- mean `completeness` across the episode's
whole `[start_range_low, end_range_high]` span, not a minimum over a handful of literal
change-point dates. `_coverage_for` (the original, min-across-a-single-day's-sources function)
is unchanged and still correctly used by `communication_change`, where "weakest link among today's
sources" is the right question, not the one this weakness was ever about. `know_claim_evaluate`
doesn't have anything downstream to re-propagate to and its fingerprint (correctly) only tracks
`signal.anomaly`/`episode.episode` content, not this code change, so recomputing after this fix
needed `derive-rebuild --deriver know_claim_evaluate` + `derive-run --global` -- the same pattern
Phase 1's own rollout already established for "the code changed, force a real recompute," not new
process. Verified against the real canonical database: all 8 `episode_evidence` claims with real
change-point evidence now show `coverage` between 0.72 and 0.99 (previously a flat 0.0 for every
one), the father's-death declared claim (empty evidence) still correctly shows no `coverage` key
at all (nothing to score). One new test (`test_episode_coverage_averages_the_range_not_the_worst_
single_day`) proves the aggregation is a genuine mean, not still a hidden floor. 379 tests pass.

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

## Media transcription: item 6 started (2026-09-25)

Schema (018, `media.transcript`) and runtime (`derive/derivers/media.py`'s
`media_transcribe_whisper`) built and shipped together in one PR, not this project's usual
plan-then-schema-then-runtime cadence -- an explicit, one-off trade-off at Ry's request to move
fast and get a long-running job going, not a new default for future work.

Local transcription via `faster-whisper` (CTranslate2, `small` model, int8, fully local CPU --
same dependency ethos as Phase 6's embedding model). `media_transcribe_whisper` is LOCAL, keyed
by `source:YYYY-MM` via `COALESCE(captured_at, created_at)` (assets have no natural month key the
way events do; `changefeed.py`'s own `WATCHED_KINDS` already uses that same expression) --
deliberately not GLOBAL/single-partition, so a crash partway through an hours-long run only loses
the one in-flight partition's work, not everything already transcribed. Reuses
`apple_export.inventory.open_apple_locator` for Apple's nested-zip locator format; a new small
helper (`_open_zip_member`) handles the simpler `<zip>!/<member>` convention every other source
uses. A file that can't be opened is recorded `status='unreadable'`; a file Whisper itself fails
on is recorded `status='failed'` -- neither crashes the partition's other files, and neither is
silently omitted (invariant 2).

**Before this ran, Ry was told plainly what the real audio population contains**: some of it is
intimate/personal recordings, at least one naming a real partner, discovered while checking real
filenames during grounding. He explicitly chose to transcribe the full library with no
exclusions. Not filtered by this code -- that was his call to make, not this session's to make for
him, and it's recorded here rather than silently acted on.

**A real, more foundational bug the first real rollout attempt found**: the first `derive-run`
against the canonical database finished almost immediately and produced only 7 transcripts (5
succeeded, 2 unreadable) out of 494 real audio assets -- `derive-status` showed zero dirty, so
nothing *looked* wrong. Diagnosed directly: **487 of 494 real audio assets (apple/facebook/google)
have neither `captured_at` nor `created_at` at all**, so they only ever belong in the
`source:undated` partition bucket -- and `scheduler.py`'s `_root_keys_from_contracts` (the
function `backfill_new_derivers` uses to seed a brand-new root deriver's initial partition space,
added this same session for the new-deriver-onboarding fix) only ever generated *dated* month keys
from a contract's `covered_range`, never the undated bucket. `know_claim_evaluate` doesn't use
dated LOCAL keys at all and `semantic_text_unit`'s events almost always have a real timestamp, so
neither earlier use of this function that same evening exposed the gap -- `media_transcribe_whisper`
is the first root deriver whose real underlying data is overwhelmingly undated. Fixed by seeding
every declared source's undated bucket unconditionally, in addition to its dated range (see
`scheduler.py`); one new assertion added to the existing backfill test proving the undated key is
now seeded, not just dated months. The real canonical database's `apple`/`facebook`/`google`
undated partitions were then marked dirty by hand and re-run to actually process the missing 487
files -- see the next dated update below for final rollout numbers.

8 new tests (`test_media.py`: zip-member reading, source dispatch including a real unreadable-path
case, deriver registration, one genuine end-to-end call through the real Whisper model on a tiny
synthetic file, and partition scoping against a real imported asset). 387 tests pass, ruff clean,
compileall clean.

## Media transcription: undated-bucket rerun (2026-09-25, in progress)

Two more real bugs found live rolling out the undated-bucket fix above, both fixed and merged
(PR #68's own follow-up commits): (1) `_open_asset_bytes` had no Facebook dispatch case at all,
falling through to a generic `!/`-split path that can't parse Facebook's `facebook-zip:{...}` JSON
locator; (2) the temp-file suffix was derived from `original_path.split("!/")[-1]` then
`Path(...).suffix` -- for Apple/Facebook, `original_path` is the *whole locator JSON blob*, not a
plain filename, producing garbage like `.m4a",5]]}` that Windows rejected as an invalid temp-file
suffix. Fixed by adding a Facebook case (reusing `facebook_export.inventory.open_facebook_locator`,
exactly parallel to Apple's existing helper) and by deriving the suffix from `assets.filename`
(always a clean plain filename regardless of locator format) via a new `_temp_file_suffix()`
helper, validated against a strict `^\.[A-Za-z0-9]{1,5}$` pattern with an `.audio` fallback.

**A second, purely operational bug** (not a code defect): the first attempt to re-run the
undated buckets with this fix used a `derive-run` process that had been launched *before* the fix
was merged to `main` (11 minutes earlier) -- Python doesn't hot-reload, so that process kept
running the old buggy code in memory and reproduced the exact same suffix-garbage/Facebook
failures on the re-dirtied partitions, even though the fix was correctly on disk. Diagnosed by
comparing the running process's start time against the merge commit's timestamp; fixed by killing
the stale process, deleting the 28 garbage rows it wrote, and launching a genuinely fresh process.

That fresh run is real, non-mocked CPU transcription of the actual canonical audio corpus (22
Apple + 6 Facebook + 459 Google files, ~8GB combined raw audio, several individual Apple Voice
Memos files 130-300MB at low-bitrate encoding, likely representing many hours each) via the
`faster-whisper` "small" model -- abandoned after 42+ hours of continuous CPU transcription with
no completion in sight (the file-size distribution is extremely long-tailed: median file ~362KB,
but a handful run to 300MB+, and CPU-only autoregressive decode of the long tail was the actual
bottleneck, not a stuck process).

**Completed 2026-09-26 via rented GPU instead of local CPU.** Same 487 real audio files (7.9GB),
same real canonical database, no mocking -- just different hardware. Two RunPod GPU pods in
sequence: an H200 (~1h53m, ~$8.63, mostly spent on two dead-end approaches -- vanilla
`openai-whisper` single-file decode, and a naive 6-process parallel attempt that made things
*worse* without NVIDIA MPS due to CUDA context-switch contention) then a much cheaper RTX 4090
(~27min, ~$0.33) once `faster-whisper`'s `BatchedInferencePipeline` was used instead of one-file-
at-a-time decode -- the real fix, since vanilla Whisper decode is autoregressive and latency-
bound per token, so raw GPU FLOPS mostly sit idle regardless of card size; batching VAD-segmented
chunks is what actually uses a GPU's parallelism. Total GPU spend: ~$9. All 483 extracted files
transcribed (455 google + 22 apple + 6 facebook succeeded; 4 google files remain `unreadable`,
the same known mbox-part extraction failures noted above -- not new). `media.transcript` now has
494 total rows (487 from this rollout + 7 pre-existing from other sources); all three undated
partitions (`google:undated`, `apple:undated`, `facebook:undated`) are `clean` in
`derive.partition_state` with real fingerprints/epoch/`derivation_run` records, indistinguishable
from a normal successful local `derive-run`.

**Real data-quality bug found and fixed post-rollout, not yet caught by any test:** 51 of the 488
`succeeded` rows contained Whisper hallucination artifacts -- the classic failure mode when the
model hits silence, noise, or non-speech audio and, instead of returning empty, loops on a
repeated token/phrase or fabricates gibberish in a random script (Georgian, Khmer, Tibetan,
Cyrillic, IPA soup were all seen). Two distinct sub-patterns: single-token/short-phrase loops
("Okay. Okay. Okay." x26, "ლლლლლ" x60) and long-phrase loops (an 8-word sentence repeated 35x).
Found by manual spot-check of random samples, not by an automated check -- there was none. Fixed
with a heuristic text cleaner (windowed non-standard-character/repeated-n-gram detection plus a
sentence-level dedup pass for phrase loops longer than 3 words) applied directly to
`media.transcript.text`; verified by re-scanning all 488 rows post-clean for zero residual
hits. 25 rows were pure hallucination front-to-back and are now empty strings (correct: that's
the right transcript of near-silent audio). This cleanup script is a one-off scratchpad tool, not
yet promoted into the deriver itself -- a future rerun of `media_transcribe_whisper` (e.g. on new
audio) would need the same fix applied again, or better, folded into the deriver's own
post-processing so it's not a manual step.

**Unexpected discovery, not a bug:** two of the transcribed files turned out to be full-length
AI-generated "deep dive" podcast narrations, not personal voice recordings -- one analyzing real
text-message chat logs between two named individuals (relationship conflict, sensitive personal
content), the other analyzing Ry's own manuscript "The Hallway." Left as-is; flagged to Ry rather
than assumed to need special handling.

**A real gap this rollout surfaced, not a bug**: Apple's official privacy export contains only 22
audio files total (verified by recursively scanning every raw export ZIP directly, not just the
canonical database) -- all Voice Memos, none from any other app. Apple's privacy-export mechanism
only ever includes Apple's own first-party apps; a third-party audio app Ry also used on his phone
was never part of this export and isn't recoverable from it. Ry is considering a full iMazing
device-backup source to close this gap, explicitly deferred to a future "v1.1" phase -- see
`ROADMAP.md`.

## Phase 7: semantic annotation (topic labeling) -- schema + runtime shipped 2026-09-25, real rollout completed 2026-09-27

`PHASE7_SEMANTIC_ANNOTATION_IMPLEMENTATION_PLAN.md` (PR #69) scoped the annotation half of the
semantic layer Phase 6 explicitly deferred. Schema + runtime (PR #70): migration 019
(`signal.annotation`), and `signal_annotation_topic` -- the codebase's first real
`final_inference="SEMANTIC"` deriver and first real use of `deriver_version.model_ref`. Topic
labels come from an 18-label closed taxonomy enforced by a GBNF grammar at the token-sampling
level via `llama-cpp-python` (`llama.cpp`, no PyTorch/CUDA, same dependency ethos as Phases 6/item
6), so an out-of-taxonomy label is structurally impossible. Model/prompt/taxonomy identity is
hashed into `model_ref`.

Per the plan's decision 6, the real production model (Phi-3.5-mini-instruct) is deliberately
**not** wired in yet -- C: was at ~24.8GB free and an external-drive migration for `DataLabData`
was in progress in parallel, so this doesn't compete for that headroom. `PDL_ANNOTATION_MODEL_PATH`
must point at a real `.gguf` file for the deriver to actually annotate anything; left unset (true
of the canonical database today), the deriver registers cleanly with placeholder `model_ref`
hashes and `compute()` raises a clear, caught error rather than crashing -- an intentional
"registered but not runnable yet" state, not a bug. Real end-to-end test coverage
(`test_annotation.py`) runs the actual `llama.cpp` completion API against a tiny (~12MB), openly
licensed test-only GGUF model downloaded via `huggingface_hub`, proving grammar-constrained
decoding, row shape, and `model_ref` hashing all genuinely work -- not the production model, per
the same deferral. Migration 019 has been applied to the real canonical database; `signal
_annotation_topic` has not yet been registered/dirtied there (deliberately held until the
transcription job above finishes, to avoid any interaction with the single active `derive-run`
process).

Also fixed a real cross-test bug this PR's own test run surfaced: `test_migrations.py`'s
`pr2_engine` fixture teardown hardcoded migrations 10-18 to restore the shared `TEST_DATABASE_URL`
database after dropping schemas for its own test -- missing migration 19 silently left
`signal.annotation` dropped for every test file that happened to run afterward in the same pytest
process. Fixed the teardown and the two hardcoded expected-version-tuple assertions it fed.

## Phase 7: real rollout against the real canonical database (2026-09-26 to 2026-09-27)

The external-drive migration finished and C: had ~31GB free by the time this rollout started --
comfortably clear of the 2.2GB model, so decision 6's deferral was lifted with Ry's explicit
go-ahead rather than silently. Downloaded the real production model directly (no repo named
`microsoft/Phi-3.5-mini-instruct-GGUF` actually exists on Hugging Face; `model_ref`'s `model_id`
is just whatever local path is configured, not a hardcoded upstream repo string, so this was a
free choice, not a deviation) -- `bartowski/Phi-3.5-mini-instruct-GGUF`'s `Phi-3.5-mini-instruct-
Q4_K_M.gguf`, the same real quantization of the real `microsoft/Phi-3.5-mini-instruct` base model
the plan named, sha256 `e4165e3a71af97f1b4820da61079826d8752a2088e313af0c7d346796c38eff5`.

**A real CPU-infeasibility finding, benchmarked before committing to a plan, same discipline item
6's local-vs-GPU decision already used.** A real-data timing test (12 representative real text
units, median-length ~4,400 chars) measured ~42s/unit on this dev machine's CPU (Ryzen 5 3600) --
extrapolated across the real 70,145-unit population, ~34 days. Not viable. Per Phase 7's own
decision 6, this crossed into "eat real GPU-rental cost and ship real personal message content off
this machine" territory -- a materially different privacy tradeoff than item 6's audio-only GPU
rental, so it was surfaced to Ry explicitly rather than decided unilaterally, mirroring the
precedent item 6's transcription content already set. Ry chose to proceed with rented GPU
annotation.

**Architecture, chosen to keep the canonical database itself off the public internet.** Rather than
tunneling Postgres to a rented pod (rejected: unnecessary exposure of the real database, and
RunPod's SSH proxy turned out not to support port-forwarding at all -- tested directly, not
assumed), the real (id, text) pairs were exported to a local gzipped JSONL file (70,145 rows,
86MB), uploaded once to the pod over HTTPS via a small purpose-built upload endpoint, annotated
there, and the (id, label, score) results downloaded the same way. The actual database write went
through the exact same production code path a local run would have used: `annotation.py`'s real
`_annotate_one` was monkeypatched to a dict lookup over the downloaded results, then the real
`derive.run()` was invoked locally -- same partitioning, clear-and-replace, fingerprinting, and
epoch stamping as any other run, the GPU pod never touched the canonical database at all.

**Three real infrastructure defects found and fixed live, none of them in this project's own
code:**
1. Two Community Cloud RunPod hosts in a row had a broken CUDA stack at the driver level
   (`cudaGetDeviceCount` returned `cudaErrorUnknown`, confirmed directly, not assumed) --
   unrelated to any code here. Switched to Secure Cloud (RunPod's own datacenters), which worked
   immediately on the first host tried.
2. A real context-window bug this rollout's own benchmark surfaced: a small fraction of real text
   units tokenize to more than `_N_CTX - _MAX_TOKENS` tokens (dense/non-English/structured content
   tokenizes less efficiently per character than typical English prose), which `llama.cpp` raises
   on rather than truncates -- and since `compute()` builds every unit in one partition via a
   single list comprehension, one such unit would have crashed its entire source/month partition,
   silently losing every other unit alongside it. Fixed in `annotation.py` itself (`_bounded_prompt`,
   tokenize-and-truncate-from-the-end with a safety margin), not just the rollout script, with a
   regression test (`test_annotate_one_bounds_a_unit_too_long_for_the_context_window`) using a
   ~100,000-char synthetic unit. This is a real, permanent fix to the production deriver, found
   because this rollout used real data at real scale, not a synthetic smoke test.
3. **Multi-process GPU parallelism failed twice, root-caused, and abandoned in favor of a
   single-stream design that actually works.** 8 workers, then 4, then 4 again under NVIDIA MPS,
   all produced a fast initial burst followed by a near-total stall at the same absolute position
   regardless of worker count or process-spawning method (`multiprocessing.Process` fork vs.
   independent `subprocess.Popen` vs. MPS-routed) -- ruling out CUDA-context-switch contention
   (item 6's original diagnosis for its own multi-process stall) as the cause here, since MPS
   exists specifically to fix that class of problem and made no difference. A definitive isolated
   test (300 real sequential units, single process) never slowed down at all, proving the model/
   GPU/data were never the problem -- only concurrent multi-process execution was. Root cause not
   fully isolated (most likely aggregate GPU memory/KV-cache pressure across concurrent contexts,
   not confirmed further given diminishing value of continuing to chase it), but the fix needed
   was empirically clear: single-stream execution. `remote_annotate.py`'s worker dispatch was
   changed from `multiprocessing.Process` to independent `subprocess.Popen` calls along the way
   (a real, permanent improvement to the rollout tooling regardless of the parallelism question,
   since it avoids fork+CUDA's well-documented bad interactions) even though the actual fix that
   mattered was `n_workers=1`.

**Real rollout result:** all 70,145 real text units annotated by the real Phi-3.5-mini-instruct
model, single-stream, in 65,184s (~18.1 hours) -- rate held in a gently-stepping band throughout
(0.52s/unit early, plateauing in stages up to ~0.93s/unit by the end; verified directly, at every
step, to be a bounded plateau rather than runaway drift, never a stall). Written back through the
real `derive.run()`: 933/933 partitions succeeded, 0 failed. `signal.annotation` now holds exactly
70,145 rows, one per real `semantic.text_unit`, an exact 1:1 match verified directly against the
canonical database (not assumed from the write-back script's own report). Real label distribution
across the full closed taxonomy: `other` 21,943, `technology` 11,931, `shopping` 5,656, `work`
5,644, `finances` 5,543, `relationships` 3,540, `logistics` 3,057, `news_politics` 3,043, `family`
2,951, `entertainment` 1,878, `education` 1,136, `food` 1,105, `health` 701, `housing` 621,
`travel` 506, `creative_projects` 347, `legal` 329, `pets` 214. All 370 tests pass, ruff clean.

Real cost: one Secure Cloud NVIDIA A40 pod (RunPod's own datacenter, CA-MTL-1, $0.49/hr; the
originally-requested RTX 4090 had zero Secure Cloud stock at request time and two Community Cloud
4090 hosts both had the broken-CUDA defect above), ~19.3 hours total pod uptime including all
diagnosis and the two failed parallelism attempts, ~$9.45 -- plus ~$0.30 across the two broken
Community Cloud pods before switching to Secure Cloud. Total real spend ~$9.75, matching the
project's own established "rent cheap GPU hardware for a bounded, disclosed cost rather than
tie up this machine for weeks" pattern item 6 already set.

## Phase 8: documents into the semantic layer -- schema + runtime shipped 2026-09-25, real rollout completed 2026-09-26

`PHASE8_DOCUMENT_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md`, found while investigating whether the
media-transcription job was re-deriving text that already exists (it partially is -- 39+ of 45
Recorder-sourced Google audio files already have real, verbatim, speaker-labeled transcripts in
`records`, confirmed by reading real rows). Migration 021 (`semantic.text_unit_document`/
`semantic.embedding_bge_small_en_v15_document`), and two new derivers mirroring Phase 6's own
`semantic_text_unit`/`semantic_embedding_bge_small` split exactly: `semantic_text_unit_document`
(HALO root, reads `records` instead of `events`, filtered to a grounded 10-namespace allowlist --
Drive documents, NotebookLM notebooks/sources, Recorder transcripts, Gemini conversations, Apple
Notes) and `semantic_embedding_document_bge_small` (LOCAL, reuses `semantic.py`'s pinned
`embed_texts()`, no second model load). `semantic-search` gains `--include-documents`, merging
both embedding spaces by distance -- an explicit opt-in, so Phase 6's already-verified
message-search behavior is unchanged by default.

**Two real bugs found building this, both fixed before merging**:
1. `started_at`/`ended_at` were initially `NOT NULL`, copying `semantic.text_unit`'s own column
   definitions -- but unlike Phase 6's events (which almost always have a real timestamp), nearly
   all of the real in-scope `records` population has no `occurred_at`/`created_at`/`updated_at` at
   all. A `NOT NULL` constraint would have forced fabricating a placeholder timestamp, exactly the
   invented-value invariant 2 already argues against. Fixed by making both columns nullable, and
   by writing `semantic_text_unit_document`/`semantic_embedding_document_bge_small`'s `compute()`
   with a real `IS NULL` branch for the undated case (copying `media_transcribe_whisper`'s own
   pattern) instead of `semantic_text_unit`'s "return empty for undated" shortcut, which is only
   valid there because Phase 6's events always have dates -- reusing it here would have silently
   produced zero document units for the whole corpus and looked like success.
2. A `%` character in migration 021's own SQL comment ("99.97%") broke `psycopg`'s parameter
   substitution (`incomplete placeholder`) the moment any test tried to apply it via
   `exec_driver_sql` -- no other migration file in this project uses a bare `%` in SQL at all.
   Found immediately by running the full test suite, not assumed safe from a smaller check;
   fixed by rewording the comment. The failure cascaded through the same shared-`TEST_DATABASE_URL`
   mechanism the migration-19 and migration-20 gaps did (`test_migrations.py`'s `pr2_engine`
   fixture drops shared schemas as part of its own test, then reapplies migrations 10+ in its
   teardown) -- fixed the same way: added `_read_migration(21)` to that teardown and bumped its
   hardcoded expected-version tuples, then manually repaired the already-broken shared test
   database (`DROP SCHEMA ... CASCADE` on all ten derived schemas + `DELETE FROM schema_migrations
   WHERE version >= 10` + `migrate`) since the damage predated the fix.

## Phase 8: rollout against the real canonical database (2026-09-26)

The media-transcription job finished (see "Media transcription: item 6 started" above), clearing
the single-active-process caution this rollout was deferred behind. `derive sync` registered and
backfilled `semantic_text_unit_document` and `semantic_embedding_document_bge_small` (939 seeded
partitions each, from the same upstream history as Phase 6's own root), then `derive run`
processed every partition to completion: 939/939 clean, zero dirty, zero failed, for both
derivers. Real rollout counts: 597 real `semantic.text_unit_document` rows and 597 real
`semantic.embedding_bge_small_en_v15_document` rows -- exact 1:1, same invariant Phase 6's message
population already verified. Breakdown by source: 448 google (Drive documents, NotebookLM,
Recorder transcripts, Gemini conversations), 149 apple (Notes) -- the grounded 10-namespace
allowlist correctly excluded every other record type, and no Facebook/OpenAI/Anthropic/iMessages
record matched the allowlist (expected: none of those sources' record types were in scope).
`semantic-search --include-documents` is now live against real data.

## Claim-template growth: relationship_state_shift (2026-09-25)

ROADMAP.md item 3's "further claim-template growth" (the `communication_change`/`episode_evidence`
pair from Phase 5's rollout was always explicitly two of the eventual set, not the whole set).
`relationship_state_shift` wraps Phase 4's already-computed `signal.interaction_state` (PELT
segmentation of each correspondent's monthly reciprocity into states) the same way
`episode_evidence` wraps episode detection: one claim per consecutive pair of states for the same
(source, correspondent) -- a correspondent whose interaction pattern only ever had one detected
state produces no claim (nothing to compare), one with two or more states produces a claim per
transition. `class` is always STATISTICAL (automatic segmentation, no human review step exists for
this data the way episodes have one); `confidence.statistical_strength` is the absolute change in
mean reciprocity index between the two states; `confidence.source_independence` is always 1,
matching `communication_change`'s own honesty precedent -- `measure.interaction_dynamics` is
computed per single source (Phase 4 decision 10), never fabricated as corroborated by more than
one; `confidence.coverage` reuses `_coverage_for_range` unchanged (zero new coverage logic).

Grounded against the real canonical database before writing any code: 77 `signal.interaction_state`
rows across 69 (source, correspondent) pairs, of which only 7 correspondents have 2+ states (8 real
consecutive-pair transitions total) -- the actual, honest small-N population this template has to
work with, not an invented one. Migration 020 widens `know.claim`'s `template` CHECK constraint
(the actual constraint name, `claim_template_check`, was confirmed directly against the database
rather than assumed from Postgres's usual default-naming convention, which would have been wrong
here). 2 new tests in `test_phase5.py`: a synthetic two-vs-one-state scenario confirming only the
real transition produces a claim (not the solo state), and a `claim_why` check confirming both
cited `signal.interaction_state` rows are independently re-fetched and match. Not yet rolled out
against the real canonical database (`know_claim_evaluate` is GLOBAL and would need a
`derive run --global` pass, deliberately deferred until the transcription job finishes, same
single-active-process caution as Phase 7).

## Operational track: encrypted backup/restore (2026-09-25)

`personal-data-lab backup`/`restore` (no separate plan doc -- a self-contained utility, not a
derivation-pipeline phase). `pg_dump`/`pg_restore` run inside the project's own Postgres container
(`docker exec personal-data-db ...`, container name overridable via `PDL_DB_CONTAINER`) rather than
requiring host-installed Postgres client tools -- this dev machine (and CI, via the same
`compose.yaml`) only ever runs Postgres in Docker, confirmed directly (`pg_dump`/`pg_restore` are
not on this machine's PATH at all). Encryption keys are passphrase-derived (PBKDF2HMAC-SHA256, 600k
iterations, a fresh random salt per backup) via the `cryptography` package, not a new external
binary dependency -- `PDL_BACKUP_PASSPHRASE` lives wherever the caller keeps it; there is no
recovery path if it's lost, by design. `restore` refuses to target the canonical database by name
unless `force=True`, reusing `config.py`'s existing `DatabaseTargetGuardError` pattern rather than
inventing a second safety mechanism.

Replaces the ad hoc unencrypted `pg_dump` this project had relied on manually until now (e.g.
`backups/20260921T195110Z/`, used once before applying migration 010). Real, non-mocked round-trip
test: a real event imported into the test database, backed up, decrypted, and restored into a
genuinely separate scratch database (`pdl_backup_restore_test`, created/dropped per test), with
the restored row's content verified to match byte-for-byte -- plus coverage for a missing
passphrase, a wrong passphrase failing to decrypt, and the canonical-database guard firing.

**A real bug the first actual production run found, same day**: the initial implementation used
`cryptography.fernet.Fernet`, which encrypts its entire input in one call and therefore holds the
whole payload in memory. Running `backup` for real against the canonical database (13GB+,
concurrently with the still-running media-transcription job) raised a genuine `MemoryError` inside
`Fernet.encrypt()` -- caught immediately because this was run for real, not left as a
theoretical concern the test suite's small-scale round-trip test could never have caught. Fixed by
switching to a fully streaming design: `pg_dump`'s stdout is read and encrypted (AES-256-CTR via
`cryptography`'s low-level `Cipher`/`CipherContext` API) in bounded 4MB chunks, so memory use stays
roughly constant regardless of database size. CTR alone gives no tamper detection, so integrity is
a separate encrypt-then-MAC step (HMAC-SHA256 over the ciphertext) verified in a first full
streaming pass over the file *before* any byte is decrypted or handed to `pg_restore` on restore --
a wrong passphrase or a corrupted file is still caught cleanly as a `BackupError`, never fed as
garbage into `pg_restore`'s stdin. All round-trip and guard tests re-verified passing against the
new implementation; a real backup of the canonical database was then re-run with the fix in place
and completed successfully: `pdl_backup_personaldata_20260925T150003Z.dump.enc`, 2,847,912,765
bytes (~2.85GB), ~7 minutes wall time, run concurrently with the still-active media-transcription
job with no interference (Postgres's own MVCC snapshot isolation, as expected). The passphrase was
generated for Ry and shown once in chat, never stored in this repo or the backup file.

## Disposability verification (invariant 20) -- two real bugs found (2026-09-26)

A real, empirical test of architecture invariant 20 ("everything in derive/signal/semantic/know
can be wiped and rebuilt from canonical evidence with zero loss"), requested directly rather than
taken on faith. Method: restored the real `pdl_backup_personaldata_20260925T150003Z.dump.enc`
backup into a scratch database (`pdl_disposability_check`), captured content checksums (MD5 over
`string_agg(row(...)::text, '|' ORDER BY <natural key>)`, deliberately excluding serial ids and
wall-clock `computed_at`/`created_at` columns -- those are never expected to reproduce byte-for-byte
across a wipe+rebuild, only content is) across 12 derived relations, ran `rebuild()` (all derivers
except `media_transcribe_whisper`, deliberately excluded and reset back to `state='clean'` --
rebuilding it would have re-run real Whisper transcription on the same machine as the actual
production transcription job), then `derive-run --global` to completion, then recomputed the same
checksums and compared.

10 of 12 relations reproduced exactly. Two real, confirmed discrepancies (confirmed by also
checksumming the real canonical database, which has been untouched in these schemas since the
backup and independently validated the checksum methodology itself for 4 of the 12 relations):

1. **`semantic.text_unit`: 12 of 70,145 real text units (0.017%) had different concatenated
   content after rebuild.** Root cause: `semantic_text_unit`'s grouping query
   (`derive/derivers/semantic.py`) sorted messages by `conversation_id, occurred_at` with no
   tiebreaker. Two messages in the same session sharing an identical timestamp (common in real
   iMessage/OpenAI data) have no guaranteed tie order across two physical copies of the same
   logical rows -- a `pg_restore`'d copy isn't guaranteed to return ties in the same order as the
   original. Fixed: added `id` as an explicit tiebreaker, both in the SQL `ORDER BY` (fixes the
   `openai`/`anthropic` whole-conversation path, which has no Python-level sort at all) and in the
   session-grouped path's Python `sorted()` key (fixes `facebook`/`imessages`). `code_version`
   bumped to "2" for the audit trail. Regression test: `test_phase6.py`'s
   `test_tied_timestamp_events_order_deterministically_by_id`.
2. **`episode.episode`: the rebuild re-proposed all 8 of canonical's already-confirmed detected
   episodes as brand-new duplicate `candidate` rows.** Root cause, and *not specific to a
   rebuild*: `detect_episode_candidates` re-clusters change points from raw signal data completely
   unconditionally on every run, with no check for whether a given date range was already decided.
   `clear_partition` deliberately only ever deletes `kind='detected' AND status='candidate'` rows
   (so a human's confirm/reject decision survives routine re-detection) -- but nothing stopped
   fresh detection from proposing an identical range as a *new* candidate sitting right alongside
   the already-confirmed one. Since `episode_detect_candidates` is a GLOBAL deriver that reruns
   whenever upstream signal data changes, this would have silently duplicated all 8 confirmed
   episodes for real the next time new data triggered a recompute -- the rebuild just made it
   happen today instead of on some future ordinary import. Fixed: `detect_episode_candidates` now
   queries existing non-candidate `kind='detected'` rows first and skips any freshly-clustered
   range that exactly matches one already decided. `code_version` bumped to "2". Regression test:
   `test_phase4.py`'s `test_confirmed_candidate_is_not_reproposed_on_redetection`.

Neither fix required any cleanup in real canonical -- canonical never actually hit the duplicate-
candidate bug (confirmed: it still shows exactly 1 candidate + 8 confirmed, no duplicates), and the
text-unit ordering bug only affects which of several equally-valid concatenation orders a tied-
timestamp session lands on, not data loss. Both `code_version` bumps are for the audit trail only:
the worker calls whatever `compute()` is currently imported for a partition's still-active
`deriver_version_id` (see `worker.py`'s `_claim_next`), so neither fix is gated behind an explicit
`derive promote` -- each takes effect automatically the next time these partitions are genuinely
recomputed, no separate real-canonical rollout step needed. The scratch database
(`pdl_disposability_check`) was dropped after the comparison was documented.

**A methodology note worth recording**: the original "before" checksum query text itself was not
preserved verbatim across a context-window compaction mid-session, so the "after" comparison had to
reconstruct an equivalent query rather than literally rerun the same one. This was validated, not
assumed -- 4 of the 12 relations' reconstructed-query checksums matched the original "before" file
exactly when run against real canonical (untouched since backup), confirming the reconstruction was
faithful before trusting any mismatch as a real finding rather than a methodology artifact.

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
2. **Phases 6, 7, 8, and item 3's `relationship_state_shift` are all now real-rolled-out against
   the real canonical database, not just schema+runtime.** Phase 6 (messages): 70,145 text units,
   70,145 embeddings. Phase 7 (topic annotation): 70,145 real annotations via a real rented-GPU
   Phi-3.5-mini-instruct pass, completed 2026-09-27 -- see "Phase 7: real rollout" above. Phase 8
   (documents): 597 text units + embeddings. `relationship_state_shift`: 8 real claims. Item 6
   (media transcription) also completed earlier. Next up, per `ROADMAP.md` items 7 and 8: **topic
   clustering** (`PHASE9_TOPIC_CLUSTERING_IMPLEMENTATION_PLAN.md`, spec published 2026-09-26, no
   model/hardware gate -- pure CPU math over embeddings already computed, can implement and run
   immediately) and **the life graph** (`PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md`, spec published
   2026-09-26 and revised twice through review to v3 the same day -- entities, relationships,
   events, decisions, and open loops via one consolidated GPU pass reusing Phase 7's now-proven
   infrastructure, sequenced to start once Phase 7's rollout was real and complete, which it now
   is).
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
