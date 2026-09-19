# Apple production-readiness acceptance report

## Result

Version 0.18.0 passes the production representation gate for the retained official Apple privacy
export. A clean PostgreSQL import and unchanged replay complete with exit 0, complete accounting,
`production_ready: true`, and zero failures, unknown members, parse warnings, or conflicts. Raw ZIP
archives remained read-only. No source payload, source path, generated inventory, runtime log,
probe, or database is committed.

This milestone does not pretend schema-agnostic records have product-specific meaning. It makes
representation readiness and interpretation depth independent, machine-readable dimensions:

- `fully_structured` / `provider_specific` identifies product-native, text, or external-asset
  adapters throughout a product group;
- `losslessly_structured` / `generic_lossless` identifies a product group containing the generic
  CSV/JSON/XML representation;
- both representations require deterministic identities, complete decoded provider fields,
  immutable raw evidence, fail-closed malformed input, provenance, and exact replay;
- unknown formats, failures, partial support, and undisposed warnings still fail the gate.

## Why parser identity did not change

The structured parser already retained all CSV cells (including duplicate headers and surplus
columns), complete JSON values, and complete XML trees. This change qualifies that accepted
representation and adds interpretation reporting; it does not change normalized objects,
fingerprints, categories, or source identity. Advancing the parser version would falsely turn the
same semantics into 897 new parser runs, so the existing parser identity is deliberately preserved.

## Corpus accounting

The retained export contains:

- 31 outer ZIP archives and 1,503,886,835 physical source bytes;
- 832 recursively observed entries;
- 18 nested ZIP containers;
- 814 classified data leaves and zero unknown members;
- 1,960,490,082 logical leaf bytes.

All bytes are accounted without extracting over the source directory. The two registered product
groups not present in this export remain explicitly unclaimed: Apple TV and Podcast Information,
and Siri.

## Clean PostgreSQL state

The isolated fresh import produced:

- 805 raw-byte source-file identities and 814 source locations;
- 897 completed member/chunk runs and no other run status;
- 476,444 records and 476,444 record versions;
- 476,467 record observations (shared stable containers legitimately have more observations than
  canonical records);
- 162,318 events, event versions, and event observations;
- 380 assets, asset versions, and asset observations;
- 161 relationships, relationship versions, and relationship observations;
- a 3,774,535,359-byte PostgreSQL database at the acceptance snapshot.

These canonical and version counts match the parser-v3 operational baseline. The small physical
database-size difference from the historical report is storage-layout noise, not semantic drift.

## Exact unchanged replay

The complete retained source was replayed without modification under the final readiness code:

- the exact replay completed in 356.78 seconds and exited 0;
- all 814 data members imported and all 814 short-circuited;
- all 897 completed run/chunk identities were reused;
- source-file, source-location, canonical, version, observation, and run counts remained unchanged;
- zero failures, unknowns, warnings, or conflicts;
- all 30 present products passed the representation gate: 27 `losslessly_structured`, three
  `fully_structured`, and two registered products `not_present_in_export`;
- interpretation remained explicit: 27 `generic_lossless`, three `provider_specific`, and two
  `not_present_in_export`;
- the command exited 0 with complete accounting and `production_ready: true`.

## Verification

- Complete PostgreSQL 18/pgvector-backed suite: 250 passed.
- Focused Apple suite: 35 passed.
- Ruff lint: clean for the whole repository.
- Python compilation: clean for source and tests.
- Dependency lock check: clean.
- Markdown relative-link validation and Git whitespace check: clean.

## Boundary and next work

Production readiness here means durable, lossless, replay-safe ingestion. Product-specific derived
meaning, cross-source claims, identity resolution, embeddings, and visual explanation remain later
layers and must cite the immutable Apple observations rather than rewrite them.

The next source-readiness task is Google: preserve the accepted 68-product/25,427-member accounting
while making the retained Nest opaque-row and Wallet asset-only dispositions explicit in the
machine-readable production gate. See [`PROJECT_STATE.md`](PROJECT_STATE.md) and
[`ROADMAP.md`](ROADMAP.md).
