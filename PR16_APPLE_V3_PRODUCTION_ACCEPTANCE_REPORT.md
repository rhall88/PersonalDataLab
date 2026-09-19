# PR16 official Apple parser-v3 corpus acceptance report

## Result

Parser v3 passed fresh real-corpus persistence, controlled interruption/resume, semantic
reconciliation, and exact unchanged replay against the retained official Apple privacy export.
Raw ZIP archives remained read-only. No source payload, source path, generated inventory,
runtime log, probe, or database is committed.

This is operational corpus acceptance, not a claim that every Apple product has a dedicated
semantic adapter. The complete replay exits 3 with `production_ready: false` because 27 present
product groups intentionally retain generic structured coverage. It has zero persistence
failures, unknown members, parse warnings, or conflicts.

## Corpus accounting

The retained export contains:

- 31 outer ZIP archives and 1,503,886,835 physical source bytes;
- 832 recursively observed entries;
- 18 nested ZIP containers, which are the only structural entries;
- 814 classified data leaves and zero unknown members;
- zero provider-guide leaves under the corrected exact root-guide rule;
- 1,960,490,082 logical leaf bytes, all processed.

Parser v2 classified 347 PDFs as provider guides. Parser v3 correctly recognizes that they are
product data and indexes all 347 as external assets. The total recursive entries and logical
bytes are unchanged; the corrected split is 814 data leaves plus 18 nested containers.

## Controlled stop and resume

The fresh parser-v3 import was stopped during a streamed Apple Media Services CSV after a
5,000-row chunk committed. The isolated PostgreSQL database retained:

- 195 source-file identities and 195 completed import runs;
- 7,135 records and exactly 7,135 record versions;
- 5,122 events and exactly 5,122 event versions;
- 180 assets and exactly 180 asset versions;
- zero staging, failed, or conflicted runs.

On resume, all 195 completed prefix transactions short-circuited and the importer continued at
the next chunk. The wall-clock window from the first committed run through the completed initial
pass was 5,610.26 seconds, including the deliberate stop and local database-bridge recovery; it
is acceptance evidence, not a performance benchmark.

## Production defect found and fixed

The first complete pass processed 809 of 814 leaves and isolated five failures to newline-only
TestFlight CSVs. Apple uses those one-byte files to represent valid empty datasets. The streaming
CSV adapter previously required a non-empty header and therefore produced no parsed export.

Parser v3 now skips leading blank rows and represents a wholly blank CSV as one empty parsed
export. This preserves raw identity, source-location provenance, run identity, and a successful
zero-row observation. A regression test reproduces the one-byte TestFlight shape. The five real
members then imported successfully from their original archive locators.

## Final canonical state

- 805 distinct raw-byte source-file identities across 814 retained source locations;
- 897 completed import runs/parser chunks and no other run status;
- 476,444 records and exactly 476,444 record versions;
- 162,318 events and exactly 162,318 event versions;
- 380 assets and exactly 380 asset versions;
- 161 relationships and exactly 161 relationship versions;
- 3,774,617,279 bytes in the final PostgreSQL acceptance database;
- zero failures, unknowns, parse warnings, or conflicts.

Relative to the historical parser-v2 report, events are unchanged. Parser v3 adds the 347 newly
indexed PDFs plus five records and relationships from the corrected representation and context
rules. No object has an unintended alternate version.

## Semantic reconciliation

- All 347 PDF leaves are distinct indexed PDF assets.
- All 22 Voice Memo leaves are external `.m4a` assets; PostgreSQL stores metadata and paths, not
  media bytes.
- Four XML leaves produce four records with complete root, attributes, text, and non-empty child
  trees.
- Three native calendar sources produce 56 records/events: 53 calendar events and 3 reminders.
  Fifty-four have normalized occurrence timestamps; two legitimately have no dated occurrence.
- Notes produce 149 canonical notes, 138 stable containers, 149 container-to-note relationships,
  and 12 container-to-attachment relationships.

The four historical duplicate-byte note occurrences resolve into two byte-identity groups: a
two-byte value at four locations and a 24-byte value at two locations. Parser v3 retains one raw
byte identity per group but creates a distinct context-aware run for every location, so unrelated
notes cannot short-circuit each other. A separate one-byte newline identity is shared by one Note
and the five empty TestFlight leaves; all six contexts likewise have distinct completed runs.

## Exact unchanged replay

The complete retained source was replayed without modification after the TestFlight repair:

- all 814 data members imported and all 814 short-circuited;
- all 897 completed run/chunk identities were reused;
- source-file, canonical, version, and import-run counts remained unchanged;
- zero failures, unknowns, warnings, or conflicts;
- complete accounting remained true;
- replay time derived from source `last_seen_at` bounds was 314.38 seconds.

The replay exits 3 rather than 0 because 27 present product groups remain honestly marked
`partially_structured`. Three present groups are fully structured (Business Chat Support, Store
Credit, and Voice Memos), and two registered groups were not present (Apple TV and Podcast
Information, and Siri). Exit 3 is the semantic-coverage gate, not a persistence failure.

## Verification

- Complete PostgreSQL 18/pgvector-backed suite: 235 passed.
- Focused Apple suite: 34 passed.
- Ruff lint: clean for the whole repository.
- Python compilation: clean.
- Controlled interruption/resume: passed.
- Corrected TestFlight differential import: passed.
- Exact unchanged replay: passed.

## Next frontier

Apple parser-v3 operational acceptance is complete for this retained corpus. Product-specific
semantic adapters and fixtures can replace generic structured coverage incrementally without
weakening the accepted evidence layer. At publication the next source-parity work was the retained
sharded OpenAI export; that work has since completed in PR17. Repair and re-acceptance of the
incomplete Anthropic raw manifest is now the immediate source-ingestion priority. See
[`PROJECT_STATE.md`](PROJECT_STATE.md) for the current baseline.
