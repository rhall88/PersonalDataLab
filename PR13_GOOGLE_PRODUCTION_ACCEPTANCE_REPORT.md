# PR13 Google production acceptance report

## Scope

PR13 validates the merged PR5–PR11 Google Takeout implementation against the retained local
export, fixes defects exposed only by production-shaped data, and records sanitized acceptance
evidence. Raw exports remained read-only. No source payload, personal value, generated log,
database, or machine-specific source path is committed.

## Corpus accounting

The retained export contains:

- 9 ZIP archives;
- 25,432 archive members;
- 25,427 data members and 5 structural members;
- 146,248,662,729 accounted bytes;
- 51 present products and 17 absent products from the 68-product registry;
- zero unknown or unsupported members.

All 51 present products route deterministically without generic fallback, raw preservation, or an
unknown/unsupported disposition. The product-level coverage classification is more precise than
the original “fully structured” shorthand: 43 products are fully structured, six are empty in the
retained export, Google Wallet is asset-indexed, and Nest is partially structured because 31
provider-malformed JSONL rows are preserved as explicit opaque events. The remaining 17 registry
products were not present and have synthetic contract coverage only.

## Restart and complete-pass evidence

A controlled interruption left 6,448 completed member transactions intact. Resuming against the
same isolated PostgreSQL database reused the completed prefix and reached the end of the corpus.
The first resumed run exposed three Calendar serialization failures and one malformed Mail-header
fingerprint failure. Focused fixes made all six Calendar members and the malformed Mail message
serializable and fingerprintable.

The subsequent complete pass processed all 25,427 data members and accounted for every byte with:

- zero failed members;
- zero unknown members;
- zero raw-preserved members;
- zero unsupported variants;
- all 51 present products explicitly dispositioned: 43 fully structured, six empty,
  one asset-indexed, and one partially structured; no generic/raw/unknown member remained.

That pass also surfaced provider-identity collisions previously hidden by cached import results.
Because a complete third read of 146.25 GB was intentionally avoided, PR13 used a clean,
production-schema differential replay of every affected member plus an identical second replay.

## Production defects fixed

1. Calendar event metadata now serializes optional end times as ISO 8601 strings.
2. Malformed email header surrogate bytes are escaped without discarding their identity.
3. Google Photos sidecars use their own stable member identity while relationships continue to
   connect them to the shared media asset.
4. Gmail messages combine Gmail thread identity with RFC Message-ID when both are available,
   preserving distinct messages when a provider export reuses an RFC Message-ID.
5. Nested NotebookLM `Sources`, `Artifacts`, and `Discovered Sources` JSON members are normalized
   as notebook documents rather than being collapsed onto the notebook root.

Parser versions were advanced for the Mail and AI/knowledge identity changes so unchanged source
bytes remain eligible for semantic reprocessing.

## Differential real-data replay

The clean replay selected 378 affected structured members: 12 reproduced Google Photos conflict
members and 366 NotebookLM members previously associated with conflicts. Results:

- first corrected import: exit 0, zero conflicts;
- identical second import: exit 0, zero conflicts;
- second import short-circuited all 378 members.

The two exact Mail messages involved in the reproduced provider-ID collision occurred in two
streaming chunks. Results:

- first corrected import: zero conflicts;
- identical second import: zero conflicts;
- all replayed Mail chunks short-circuited.

## Warning disposition

A parse-only scan rechecked all 411 Drive members and 13 Nest members without database writes:

- Drive: five bounded searchable-text projections; complete provider structures and external
  source assets remain preserved.
- Nest: 31 non-JSON JSONL lines explicitly retained as opaque provider text.
- failures: zero.

These warnings describe intentional bounded or opaque projections, not skipped source data.

## Verification

- Complete test suite with disposable PostgreSQL: 185 passed.
- Unit-only run: 150 passed, 35 integration tests skipped as designed without a database URL.
- Focused Google regression suite: 24 passed.
- Ruff: clean.
- Python compilation: clean.
- Exact corrected real-data replay: green and idempotent.

## Boundary and next frontier

PR13 changes no canonical schema and commits no acceptance artifacts. Google ingestion is
operationally accepted for this retained export through the complete-pass plus clean differential
replay evidence above, with the product-level semantic qualifications recorded in
[`GOOGLE_PRODUCT_COVERAGE.md`](GOOGLE_PRODUCT_COVERAGE.md). At publication the next frontier was
official Apple inventory; Apple, iMessages, and OpenAI have since landed in PR14–PR17. See
[`PROJECT_STATE.md`](PROJECT_STATE.md) and [`ROADMAP.md`](ROADMAP.md) for current sequencing.
