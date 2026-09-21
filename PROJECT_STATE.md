# PersonalDataLab project state

Updated 2026-09-20, after schema version 9 added reversible identity resolution over the canonical
database. Version `0.22.0` adds five `identity-*` commands and the `identity_links` schema;
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

Fresh databases initialize directly at schema version 9. `personal-data-lab migrate` upgrades a
PR2 database idempotently through the tracked migrations:

| Version | Purpose |
|---:|---|
| 3 | cumulative import runs, source locations, authority/lifecycle fields, and observations |
| 4 | non-null namespaced canonical identities and uniqueness constraints |
| 5 | lossless conversation/event/attachment versions and version-backed observations |
| 6 | universal durable records and external assets, each with versions and observations |
| 7 | provenance-bearing, versioned relationships |
| 8 | `search_vector tsvector` + GIN index + maintenance trigger on conversations/events/records/assets |
| 9 | `records.actor_person_id` (matching the existing `events` column) + the `identity_links` table for reversible identity merges |

Apple, Facebook, iMessages, OpenAI, and Anthropic v2 required no later schema migration because the
version-7 universal record, event, asset, and relationship model already expresses their data.
Migration 8 requires no importer-side changes either — every existing source becomes searchable
purely from its already-existing canonical columns. See [`QUERY_SPEC.md`](QUERY_SPEC.md). Migration
9 likewise requires no importer changes: identity links are asserted after the fact by a human, not
inferred at import time. See [`IDENTITY_RESOLUTION_SPEC.md`](IDENTITY_RESOLUTION_SPEC.md).

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
2. **Add derived understanding.** Claims, cross-source correlations, OCR/transcription, chunks,
   and embeddings belong in separately versioned, disposable layers whose outputs cite immutable
   observations.
3. **Continue the operational track.** Encrypted backup/restore, integrity audits, sensitive-field
   controls, incremental scheduling, performance budgets, derived-data invalidation, and corpus
   exportability remain permanent requirements.

Do not start MCP, an agent layer, or a second analytical source of truth before ordinary canonical
queries are fast, evidence-citing, access-controlled, and covered by the same migration and replay
discipline.
