# PersonalDataLab project state

Updated 2026-09-20, after the canonical database was rebuilt fresh with all six accepted source
families, including official Facebook export ingestion. Version `0.19.0` adds the `facebook_export`
module; version `0.18.0` qualified Apple lossless structured ingestion and reported interpretation
depth separately. PR7–PR11 were stacked Google changes that reached `main` through PR12. Their
remote feature branches remain history, not evidence of unfinished PRs.

This file is the current implementation summary. Specifications define contracts, coverage pages
record format support, and PR reports preserve milestone evidence from the time each PR landed.
When a historical report's “next frontier” differs from this file or [`ROADMAP.md`](ROADMAP.md),
this file and the roadmap are authoritative for current sequencing.

## Canonical database baseline (2026-09-20)

One durable database — `personaldata`, on the dedicated `pdl_canonical_postgres_data` Docker
volume (never `repo_postgres_data`, which still carries pre-rebuild acceptance/dev residue) — holds
all six accepted source families at once, imported directly from the immutable raw exports under
`DataLabData\` by the current importer code, not copied from any historical acceptance database.
Schema version 7. Every `import_runs` row across every source is `completed`; zero
`staging`/`finalizing`/`partial`/`failed`/`conflicted` rows exist.

| Source | Import runs | Records | Events | Assets | Relationships | Exit |
|---|---:|---:|---:|---:|---:|---:|
| Anthropic | 18 | — | 9,748 | 601 | 10,014 | 0 |
| OpenAI | 1,760 | — | 25,510 | 2,639 | — | 0 |
| iMessages | 35 | — | 71,789 | 3,136 | 75,928 | 0 |
| Apple | 897 | 476,444 | 162,318 | 380 | 161 | 0 (`production_ready: true`) |
| Google | 23,333 | 122,293 | 164,032 | 13,983 | 115,221 | 0 |
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

All six sources' canonical counts above are current as of this baseline and have each been
verified stable under a second identical import pass against the shared canonical database.

## Current source-family coverage

| Source family | Current status | Accepted evidence and boundary |
|---|---|---|
| Google Takeout | Operationally accepted in PR13; Nest semantic gap closed 2026-09-20 | The 68-product registry has 51 present and 17 absent products in the retained 146.25 GB export. All 25,427 data members route without unknown, generic-fallback, unsupported, or raw-preserved members. Current product status is 44 fully structured, six empty, one asset-indexed, and 17 not present. Nest's 31 rows that PR13 retained as explicit opaque text (one extra layer of provider backslash-escaping) are now mechanically recovered into their real structured content; the asset-indexed Wallet surface (one content-addressed PDF) was already a correct, final disposition rather than a gap. See [`GOOGLE_PRODUCT_COVERAGE.md`](GOOGLE_PRODUCT_COVERAGE.md) and [`PR13_GOOGLE_PRODUCTION_ACCEPTANCE_REPORT.md`](PR13_GOOGLE_PRODUCTION_ACCEPTANCE_REPORT.md). |
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

Fresh databases initialize directly at schema version 7. `personal-data-lab migrate` upgrades a
PR2 database idempotently through the tracked migrations:

| Version | Purpose |
|---:|---|
| 3 | cumulative import runs, source locations, authority/lifecycle fields, and observations |
| 4 | non-null namespaced canonical identities and uniqueness constraints |
| 5 | lossless conversation/event/attachment versions and version-backed observations |
| 6 | universal durable records and external assets, each with versions and observations |
| 7 | provenance-bearing, versioned relationships |

Apple, Facebook, iMessages, OpenAI, and Anthropic v2 required no later schema migration because the
version-7 universal record, event, asset, and relationship model already expresses their data.

## Verification state

- The repository currently collects 285 tests: non-integration tests plus PostgreSQL-marked
  integration tests, including the 13-test focused Facebook suite.
- The Facebook production-readiness change passes all 285 tests against the local PostgreSQL
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

1. **Re-run Google against the canonical database.** The Nest recovery fix changes decoded content
   for 31 previously-opaque rows, which will mint new canonical records/events alongside the old
   opaque ones (never deleting them, per the immutable-version contract) rather than editing them in
   place. Re-import and re-verify idempotency so the canonical database reflects the fix.
2. **Add a unified query foundation.** Build provenance-aware full-text search over canonical
   conversations, events, records, document text, and asset metadata before introducing agents or
   embeddings.
3. **Add reversible identity resolution.** Provider identities may be linked to people/accounts/
   devices only with inspectable evidence and manual merge/split controls. Never rewrite provider
   identity or source truth.
4. **Add derived understanding.** Claims, cross-source correlations, OCR/transcription, chunks,
   and embeddings belong in separately versioned, disposable layers whose outputs cite immutable
   observations.
5. **Continue the operational track.** Encrypted backup/restore, integrity audits, sensitive-field
   controls, incremental scheduling, performance budgets, derived-data invalidation, and corpus
   exportability remain permanent requirements.

Do not start MCP, an agent layer, or a second analytical source of truth before ordinary canonical
queries are fast, evidence-citing, access-controlled, and covered by the same migration and replay
discipline.
