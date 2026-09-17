# PR15 iMessages device-export production acceptance report

## Result

The independently acquired iMazing Messages CSV source is production-ready for the retained
corpus. It remains a distinct `imessages` source family from the official Apple privacy export,
while normalized metadata records Apple as the logical provider and iMazing CSV as the evidenced
acquisition method.

Raw CSVs remained read-only. No source payload, contact identifier, chat title, source path,
generated inventory, runtime log, temporary probe, or database is committed.

## Source accounting

- 2 classified CSV files;
- 11,913,887 physical source bytes;
- 67,917 parsed message rows;
- 103 chat sessions across the two retained exports;
- 0 physical attachment files and 3,136 attachment references;
- 0 unknown files, malformed rows, or unaccounted bytes.

The files use the exact observed 17-column iMazing Messages schema. The retained CSVs contain no
device serial, backup identity, export timestamp, timezone, or completeness manifest. The import
therefore uses partial-snapshot semantics and an explicitly supplied IANA timezone; it invents no
missing device evidence.

## Semantic coverage

Parser v2 normalized:

- 67,917 incoming, outgoing, and notification message events;
- 103 conversations and 70 distinct participant identities;
- 3,486 added-reaction events and 12 removed-reaction lifecycle events;
- 364 edit events and 10 deletion events;
- 708 reply relationships: 704 resolved to one exact message and 4 preserved as explicit
  ambiguous reply-reference records;
- 3,136 message-to-attachment relationships and reference-only assets.

The four unresolved replies each have multiple indistinguishable target rows at the same
conversation timestamp. The importer preserves the provider reference rather than choosing a
target by ingestion order. All 3,498 reaction entries are structurally parsed.

Six exact duplicate CSV rows remain six distinct message observations through deterministic
same-tuple occurrence identity. Mutable body, status, reaction, edit, and deletion fields do not
participate in logical identity.

## Controlled stop and resume

The final parser-v2 import was stopped after its first committed 2,000-row chunk. The database
retained:

- 1 completed import run;
- 48 conversations;
- 2,001 normalized events;
- 30 participant records;
- 2,123 relationships.

On resume, that chunk short-circuited and the remaining 34 chunks completed normally. Resumed
completion took 766.57 seconds across both files. The resulting summary reported complete
accounting, exit code 0, and `production_ready: true`.

## Final canonical state

- 2 source-file identities and 35 completed chunk runs;
- 103 conversations and exactly 103 conversation versions;
- 71,789 events and exactly 71,789 event versions;
- 74 records and exactly 74 record versions;
- 3,136 assets and exactly 3,136 asset versions;
- 75,928 relationships and exactly 75,928 relationship versions;
- 468,915,903 bytes in the final PostgreSQL acceptance database;
- zero failures, unknowns, warnings, or conflicts.

Event totals are 35,965 incoming messages, 31,915 outgoing messages, 37 notifications, 3,486
reaction additions, 12 reaction removals, 364 edits, and 10 deletions.

## Exact unchanged replay

The complete retained source was replayed without modification:

- all 2 CSV files short-circuited;
- all 35 parser chunks matched completed runs;
- replay importer time was 13.65 seconds after the final semantic-accounting gate;
- exit code 0 and `production_ready: true`;
- canonical, version, source-file, and import-run counts remained unchanged.

The replay updates ordinary source-location `last_seen_at` provenance but creates no canonical
object, distinct version, or import run.

## Verification

- Complete PostgreSQL-backed test suite: 234 passed.
- Focused iMessages unit suite: 11 passed.
- Real-data parse-only pass: 2/2 files and 67,917/67,917 messages, zero warnings.
- All 3,498 reactions structurally parsed.
- Ruff lint: clean for the whole repository.
- Ruff formatting: clean for every changed Python file.
- Python compilation: clean.
- Controlled stop/resume: passed.
- Exact unchanged replay: passed.

## Boundary and next frontier

This acceptance covers the retained iMazing CSV evidence only. RSMF, raw iOS `sms.db`, PDF,
Excel, and text exports were not supplied and are not claimed. The retained CSV references media
but contains no attachment bytes; the database therefore records honest unavailable/reference-
only assets.

The next source-ingestion frontier is bringing existing OpenAI and Anthropic imports to the same
coverage and real-export acceptance standard. Unified search and evidence-backed identity
resolution follow source parity; neither may rewrite iMessages source truth.
