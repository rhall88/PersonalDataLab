# Anthropic account-export ingestion

This specification defines the production ingestion boundary for official Anthropic account
exports. The retained source of truth is the untouched manifest JSON together with the local ZIP
archives named by `data_files`. Signed download URLs are neither called nor persisted.

The production CLI uses parser version `anthropic-v2` and snapshot scope
`anthropic_account_export`. The PR2-era `anthropic-v1` parser remains a compatibility API so its
historical completed runs cannot be mistaken for v2 evidence.

## Source and inventory contract

`personal-data-lab inspect-anthropic <manifest>` performs a read-only inventory before any
database connection is needed. It validates:

- one local JSON manifest with a `data_files` array;
- safe sibling ZIP filenames and one physical local archive for every declaration;
- the five observed categories: `conversations`, `design_chats`, `light_metadata`, `memories`,
  and `projects`;
- safe, unique, unencrypted ZIP member paths;
- the SHA-256 digest and byte count of the manifest, every declared archive, and every archive
  member;
- explicit disposition of every archive member as structured JSON or a source-backed binary
  asset; and
- separately reported ZIPs beside the manifest that are not declared by it.

JSON output hides the manifest path, archive filenames, archive paths, member paths, and member
filenames by default. `--include-paths` is an explicit disclosure switch. Table output uses only
opaque content-derived IDs. Conversation text and provider payloads are never written to progress
logs or inventory reports.

An undeclared sibling ZIP is counted as an unknown source file and blocks production readiness.
Missing archives, unsafe paths, duplicate members, encrypted members, invalid ZIPs, and malformed
required JSON fail validation without echoing provider payloads.

## Parsing and persistence

The importer preserves the source manifest as a sanitized record containing opaque archive IDs
and category names, not signed URLs or local paths. Each declared archive remains one
content-addressed source file. Persistence is split into restart-safe transactions:

- the sanitized manifest is one transaction;
- binary archive members are imported as individual source-backed assets;
- ordinary conversation archives are split into deterministic 25-conversation chunks;
- design-chat, project, and memory JSON members are imported independently; and
- the small coupled `users.json` plus `login_history.json` surface is reconciled together.

Every transaction has a deterministic `anthropic-v2/...` category. A controlled interruption
therefore leaves completed chunks durable; rerunning short-circuits completed run identities and
continues with the remaining work. Source absence is not deletion, and no complete-snapshot
tombstone inference is performed.

## Normalized semantics

Ordinary message text comes only from `content[type=text]` blocks joined with newlines. The full
provider content-block list, attachment/file metadata, sender, parent reference, and any differing
top-level export text remain in `metadata.anthropic`.

The importer emits provider-evidenced relationships for:

- message parent edges when the referenced message is present in the export;
- message attachment and file references;
- project-to-document membership; and
- design-chat-to-project membership.

An attachment or file reference without retained bytes becomes an explicit zero-byte unavailable
asset with an opaque deterministic identity. If an explicit provider path exactly matches a binary
member in the retained archive, the relationship points to that source-backed asset. The importer
does not guess filename matches, fetch remote objects, invent filesystem paths, or store binary
payloads in PostgreSQL.

JSON members outside the currently observed `conversations` or `light_metadata` contract are
preserved as generic structured records with a parse warning rather than silently skipped. That
warning blocks the production-ready gate until the surface receives an evidence-backed adapter.

## Exit codes and acceptance gate

| Code | Meaning |
|---:|---|
| 0 | complete accounting, no failures, unknowns, warnings, or conflicts |
| 2 | ambiguous changed payloads were preserved as conflicts; canonical truth stayed unchanged |
| 3 | persistence completed but warnings or an accounting/readiness gate remains |
| 4 | one or more declared sources or members failed to parse or persist |
| 5 | undeclared/unknown source files remain |

Production readiness requires exact source/member accounting, zero undeclared archives, zero
failures, zero parse warnings, zero conflicts, and exact asset availability accounting.

The synthetic regression suite covers all five observed categories, private output defaults,
unsafe member paths, undeclared archives, conversation chunking, available and unavailable assets,
and provider-evidenced relationships. A PostgreSQL integration test covers committed interruption,
resume, exact replay, and version-count stability.

PR19 completed fresh retained-corpus acceptance with six source files, eight members, 269
conversations, 9,748 events, 601 unavailable assets, and 10,014 relationships. The controlled stop,
resume, and exact replay passed with exit 0, and every run/version/observation count remained stable.
See [`PR19_ANTHROPIC_PRODUCTION_ACCEPTANCE_REPORT.md`](PR19_ANTHROPIC_PRODUCTION_ACCEPTANCE_REPORT.md).

## Schema boundary

No schema migration is required. Schema version 7 already represents conversations, events,
records, external assets, relationships, immutable versions, observations, and restart-safe import
runs. Canonical fingerprints continue to exclude acquisition paths, export timestamps, and parser
execution order.
