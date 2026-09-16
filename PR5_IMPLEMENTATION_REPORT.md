# PersonalDataLab PR5 implementation report

## Outcome

Implemented the Google Takeout Framework scope required by section 31 of the master contract.
The remaining 67 product-semantic adapters were intentionally not implemented in this phase.

## Files changed

- Documentation: `README.md`, `GOOGLE_TAKEOUT_COMPLETE_INGESTION_SPEC.md`,
  `GOOGLE_TAKEOUT_FRAMEWORK_ARCHITECTURE.md`, `GOOGLE_PRODUCT_COVERAGE.md`
- Packaging: `pyproject.toml`, `uv.lock`
- Schema: `db/init/002_schema.sql`, `db/migrations/006_google_takeout_framework.sql`
- Core models/persistence: `models.py`, `fingerprints.py`, `database.py`, `migrations.py`
- Google compatibility: `google_importer.py`, `cli.py`
- Framework package: `google_takeout/__init__.py`, `products.py`, `inventory.py`,
  `adapters.py`, `orchestrator.py`
- Tests: `test_google_takeout_framework.py`, `test_models.py`, `test_fingerprints.py`,
  `test_database_integration.py`, `test_migrations.py`

## Migration and normalized models

Migration 006 adds canonical `records` and `assets`, compatibility source-file joins, immutable
distinct versions, and exact version-backed observations. `NormalizedRecord` implements durable
provider objects. `NormalizedAsset` stores metadata and a resolvable external locator, never
binary content. Both use the existing authority, conflict, lifecycle, run short-circuit, version
reuse, and no-absence-deletion semantics.

## Adapter and registry framework

`GoogleTakeoutAdapter` defines adapter name/version, product keys, supported patterns, probe,
parse, support level, and representation kind. `AdapterRegistry` routes deterministically and
raises on semantic ambiguity. The default registry contains:

- `MyActivityAdapter`: wraps the PR4 streaming parser as the first fully structured adapter.
- `GenericStructuredAdapter`: losslessly preserves bounded known-product JSON/CSV/XML as
  `generic_google_record` and reports `partially_structured`.

The permanent registry contains exactly 68 unique canonical keys and all 68 contracted Takeout
display names. Import-time validation and tests prevent a 67-product registry from passing.

## Inventory behavior

`personal-data-lab inspect-google <source>` accepts one ZIP, a directory of split ZIPs, or an
extracted Takeout. ZIP inventory reads central-directory metadata only. Every member reports
export identity/timestamp, archive and ordinal, product, path/name/extension, compressed and
uncompressed size, CRC, file/directory status, candidate adapter, support status, byte-inspection
requirement, and binary/structured-text hints.

Example table:

```text
Google Takeout Inventory

Product       Members  Size   Adapter / disposition
------------  -------  -----  ---------------------
Calendar      1        42 B   generic_structured
Drive         1        3 GB   drive_adapter
My Activity   2        8 MB   my_activity
Unknown       1        12 B   inspect

Accounted members: 8/8
Accounted bytes: 3.0 GB
```

The accounting invariant is validated in the inventory model:

```text
classified_members + intentionally_ignored_structural_members + unknown_members
== all_archive_members
```

## Routing and coverage

`personal-data-lab import-google-all <source>` inventories first and handles members serially:

1. PR4 activity patterns route to `MyActivityAdapter`.
2. Known-product JSON/CSV/XML up to 64 MiB routes to the generic fallback.
3. Known unmatched files remain explicitly `raw_preserved`.
4. Unknown/unsafe members remain explicit and cause exit code 5.
5. Each matched member parses and commits independently; a failed member rolls back only itself.

The final JSON contains all 68 products (including `empty_in_export` and
`not_present_in_export`) plus discovered/processed members, object counts, bytes, warnings,
unsupported variants, failures, conflicts, reconciliation outcomes, versions, timing, and exact
failed-member locators. Exit precedence is failure (4), unknown (5), unsupported (3), conflict
(2), success (0).

## PR4 compatibility

The existing `personal-data-lab import-google` command and standalone My Activity source shapes
remain supported. My Activity normalization, identities, streaming JSON decoder, HTML fallback,
progress behavior, and one-member transaction semantics were not replaced. The framework calls
the same parser through a thin adapter.

## Verification

- Test collection contains 144 tests: 110 ordinary tests and 34 live PostgreSQL integration
  tests. The recovered worktree source passes all 110 ordinary tests locally. The 34 integration
  tests are the required GitHub CI merge gate because the local Docker runtime was unavailable.
- The earlier recovery build reported all PostgreSQL 18 + pgvector integration tests passing,
  including fresh initialization, PR2-to-PR6 migration parity, and migration idempotency. GitHub
  CI must reproduce that result on the exact PR head before merge.
- Ruff: `ruff check .` passed against the recovered worktree.
- Compile: all 27 Python source and test modules compiled in memory successfully.
- Build: `personal_data_lab-0.5.0-py3-none-any.whl` built successfully from the recovered
  worktree. SHA-256: `9198F80911A97425F4643D6AE2BCCC1D5620191A970F00F1C56572712F7D0FF0`.

## Recovery provenance

PR5 was recovered onto `feature/google-takeout-framework` from `main` at
`cf51309d27ab291378e2e81a1b6d58ccbc7a3637`. The recovery input was:

```text
PersonalDataLab-PR5-source-final-20260912T0848Z.zip
SHA-256: 503DCF39B7862C96DC7A87531EF1240E4C4AAC13B2EF5642E3D2AD1CAD33C1F0
```

The source ZIP and prior wheel hashes embedded in the older report did not match the retained
artifacts and are superseded by the independently recorded recovery manifest. Final release
artifacts must be hashed after the final commit; a source archive must not claim an authoritative
hash of itself from inside that same archive.

## Real-data acceptance

The recovered code reproduced the retained Takeout inventory from the currently organized
`Google` source directory without extracting or modifying any export:

```text
Archives:                 9
Products detected:       51
All members:         25,432
Classified members:  25,427
Structural members:       5
Unknown members:          0
Accounted members:   25,432 / 25,432
Bytes accounted:    146,248,662,729
```

The retained full-import acceptance run records a successful resume with 11,461 imported
members, 13,966 raw-preserved members, zero failures, and zero unknowns. Its unchanged second run
short-circuited all 11,461 imported members, created zero inserts and zero versions, and reused
182,999 existing versions. Exit code 3 is expected because known unsupported/raw-preserved
product variants remain; it is not a claim of complete semantic support.

No source export, generated inventory, acceptance log, database dump, virtual environment, wheel,
or source ZIP is committed by PR5.

## Known limitations

- Product-semantic support beyond My Activity is deliberately deferred to PR6-PR11.
- The generic fallback is capped at 64 MiB and performs whole-member parsing; larger structured
  files remain raw-preserved until an incremental product adapter exists.
- Binary members are inventoried without forced hashing. Future product adapters must hash when
  durable content identity/deduplication requires it; CRC is never treated as cryptographic ID.
- The framework tables support external asset references, but the retained real-data PR5 run
  indexed zero assets; product-specific asset promotion remains future work.
- Local PostgreSQL verification could not be repeated during recovery because Docker Desktop
  failed before starting its engine. The repository's PostgreSQL-backed GitHub CI is therefore a
  mandatory merge gate for the exact PR head.

## Exact recommended PR6 scope

Implement empirically validated adapters, synthetic fixtures, and real-data canaries for:

1. Timeline
2. Maps
3. Maps (your places)
4. My Maps
5. Saved
6. Calendar
7. Contacts
8. Tasks

PR6 should add explicit place/list/map/task relationships supported by provider evidence, parse
ICS recurrence/timezones without unbounded occurrence expansion, and preserve provider-native
fields alongside normalized records/events.
