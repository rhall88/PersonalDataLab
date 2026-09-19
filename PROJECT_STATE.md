# PersonalDataLab project state

Updated 2026-09-18 from `origin/main` at `22f24b3`, after the project-state documentation in PR18
merged. PRs 1–18 are merged. Version `0.17.0` adds the Anthropic v2 production-ingestion path and
synthetic acceptance described below; fresh retained-corpus acceptance is still pending. PR7–PR11
were stacked Google changes that reached `main` through PR12. Their remote feature branches remain
history, not evidence of unfinished PRs.

This file is the current implementation summary. Specifications define contracts, coverage pages
record format support, and PR reports preserve milestone evidence from the time each PR landed.
When a historical report's “next frontier” differs from this file or [`ROADMAP.md`](ROADMAP.md),
this file and the roadmap are authoritative for current sequencing.

## Current source-family coverage

| Source family | Current status | Accepted evidence and boundary |
|---|---|---|
| Google Takeout | Operationally accepted in PR13 | The 68-product registry has 51 present and 17 absent products in the retained 146.25 GB export. All 25,427 data members route without unknown, generic-fallback, unsupported, or raw-preserved members. Current product status is 43 fully structured, six empty, one asset-indexed, one partially structured (Nest), and 17 not present. See [`GOOGLE_PRODUCT_COVERAGE.md`](GOOGLE_PRODUCT_COVERAGE.md) and [`PR13_GOOGLE_PRODUCTION_ACCEPTANCE_REPORT.md`](PR13_GOOGLE_PRODUCTION_ACCEPTANCE_REPORT.md). |
| Official Apple privacy export | Parser-v3 operational acceptance completed in PR16 | All 814 data leaves across 31 outer ZIPs and 18 nested containers persist, resume, and replay exactly with zero failures, unknowns, warnings, or conflicts. Semantic coverage is intentionally incomplete: 27 present product groups are partially structured; Business Chat Support, Store Credit, and Voice Memos are fully structured; Apple TV and Podcast Information and Siri were absent. The honest full replay result is exit 3 and `production_ready: false`. See [`APPLE_PRODUCT_COVERAGE.md`](APPLE_PRODUCT_COVERAGE.md) and [`PR16_APPLE_V3_PRODUCTION_ACCEPTANCE_REPORT.md`](PR16_APPLE_V3_PRODUCTION_ACCEPTANCE_REPORT.md). |
| iMessages device export | Production-ready for the retained iMazing CSV corpus in PR15 | Two CSVs, 67,917 message rows, message/reply/reaction/edit/deletion semantics, explicit timezone input, controlled resume, and exact replay passed. The source contains attachment references but no attachment bytes. RSMF, raw iOS `sms.db`, PDF, Excel, and text exports are not claimed. See [`IMESSAGES_COVERAGE.md`](IMESSAGES_COVERAGE.md). |
| Official OpenAI export | Production-ready for the retained manifest-based export in PR17 | All 1,724 physical members, 1,187 conversations, 25,510 message events, structured JSON, retained and unavailable assets, parent edges, and message-asset edges are accounted. Controlled resume and exact replay pass with exit 0. The legacy standalone `conversations.json` path remains compatibility-only and does not provide whole-export acceptance. See [`OPENAI_INGESTION_SPEC.md`](OPENAI_INGESTION_SPEC.md) and [`PR17_OPENAI_PRODUCTION_ACCEPTANCE_REPORT.md`](PR17_OPENAI_PRODUCTION_ACCEPTANCE_REPORT.md). |
| Anthropic export | V2 production path implemented with synthetic acceptance; retained-corpus acceptance pending | `inspect-anthropic` now performs privacy-safe manifest/archive/member/byte accounting. The `anthropic-v2` importer preserves the five observed categories, commits deterministic restart-safe units, adds source-backed and reference-only assets, and emits parent, attachment/file, project-document, and design-chat/project relationships. The legacy v1 parser remains isolated. The 208-test non-integration suite passes and guarded PostgreSQL resume/replay coverage is present, but fresh real-export execution is still required before an empirical production-ready claim. See [`ANTHROPIC_INGESTION_SPEC.md`](ANTHROPIC_INGESTION_SPEC.md). |

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
- completed or conflicted run identities short-circuit exactly. Google, Apple, iMessages, OpenAI,
  and the Anthropic v2 production path use restart-safe member or bounded-chunk transactions. The
  legacy Anthropic v1 compatibility parser retains its historical all-archive transaction boundary.

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

Apple, iMessages, OpenAI, and Anthropic v2 required no later schema migration because the version-7
universal record, event, asset, and relationship model already expresses their data.

## Verification state

- The repository currently collects 249 tests: 208 non-integration tests and 41 PostgreSQL-marked
  integration tests.
- The current Anthropic-v2 change passed all 249 tests against the local PostgreSQL 18/pgvector
  test service after migrating it to schema version 7. The guarded Anthropic test proves a
  committed 25-conversation chunk survives interruption, resume reaches the complete 30-item
  synthetic fixture, and exact replay leaves observation/version counts stable.
- The latest merged PR17 acceptance evidence records all 242 tests passing against PostgreSQL
  18/pgvector, with Ruff and compilation clean.
- Source-specific real-corpus reports record Google PR13, Apple parser-v3 PR16, iMessages PR15,
  and OpenAI PR17 acceptance. Private source data, generated inventories, logs, and databases are
  intentionally not committed.

## Outstanding work in priority order

1. **Complete Anthropic retained-corpus acceptance.** Run the new privacy-safe inventory against
   the retained manifest, repair only evidence-backed defects, perform a fresh PostgreSQL import,
   prove controlled stop/resume and exact replay, reconcile every source/member/object count, and
   publish a sanitized acceptance report. Do not describe v2 as empirically production-ready until
   this gate passes.
2. **Add a unified query foundation.** Build provenance-aware full-text search over canonical
   conversations, events, records, document text, and asset metadata before introducing agents or
   embeddings.
3. **Add reversible identity resolution.** Provider identities may be linked to people/accounts/
   devices only with inspectable evidence and manual merge/split controls. Never rewrite provider
   identity or source truth.
4. **Add derived understanding.** Claims, cross-source correlations, OCR/transcription, chunks,
   and embeddings belong in separately versioned, disposable layers whose outputs cite immutable
   observations.
5. **Deepen known partial coverage.** Replace Apple's 27 generic product groups with dedicated
   semantic adapters as evidence and fixtures permit, and improve Google's partial Nest handling
   without fabricating repairs for malformed provider rows.
6. **Continue the operational track.** Encrypted backup/restore, integrity audits, sensitive-field
   controls, incremental scheduling, performance budgets, derived-data invalidation, and corpus
   exportability remain permanent requirements.

Do not start MCP, an agent layer, or a second analytical source of truth before ordinary canonical
queries are fast, evidence-citing, access-controlled, and covered by the same migration and replay
discipline.
