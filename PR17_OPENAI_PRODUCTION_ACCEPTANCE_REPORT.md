# PR17 official OpenAI production acceptance report

## Result

The retained official OpenAI data export is production-ready. The importer passed complete
manifest accounting, real-corpus PostgreSQL persistence, controlled interruption/resume,
production-defect repair, semantic reconciliation, and exact unchanged replay with exit code 0.

The original ZIP remained read-only. No source payload, conversation content, title, source path,
generated inventory, runtime log, probe, or database is committed.

## Corpus accounting

- 1 untouched ZIP containing 1,724 physical members and 2,696,770,887 uncompressed physical
  bytes;
- 1,712 logical files and 2,696,328,179 logical bytes;
- 12 physical conversation shards grouped as one logical conversations file;
- 1 manifest, 21 other structured JSON members, 1,689 binary asset members, and 1 rendered HTML
  member;
- zero unknown members or unaccounted bytes.

The conversation shards contain 1,187 conversations, 26,697 mapping nodes, and 25,510
message-bearing nodes. Fifty-six provider message IDs occur in two conversations; production
identity therefore uses conversation plus mapping-node ID rather than the provider message ID.

## Semantic coverage

The import normalized:

- 1,187 conversations and 25,510 message events;
- 24,321 message-parent relationships;
- 1,135 full-fidelity structured JSON records;
- 1,690 exported assets, comprising 1,689 binary members and 1 rendered HTML member;
- 2,242 message-asset relationships, of which 1,241 resolve to retained bytes and 1,001 preserve
  unavailable provider references;
- 949 distinct unavailable-asset placeholders;
- 1,039 library-record-to-exported-asset relationships;
- 27,602 relationships in total.

The retained ZIP has 1,688 `.dat` files. `library_files.json` contains 1,095 records: 1,039 have
retained exported bytes and 56 do not. Asset bytes stay in the source ZIP; PostgreSQL stores only
external references, hashes, sizes, metadata, and provenance.

## Controlled stop and resume

The fresh import was deliberately stopped after 250 physical members began processing. The
isolated database retained 250 completed import runs and source locations, 245 distinct raw-byte
source identities, and 250 assets with exactly 250 versions and observations.

On resume, those 250 members short-circuited. The remaining import exposed two conversation
shards containing literal NUL characters in provider message data. PostgreSQL text and JSONB
cannot store U+0000, so four later conversation chunks failed while earlier committed chunks and
all unrelated members remained intact.

The parser now converts literal NUL to the visible representation `\u0000` recursively in text
and JSON metadata. This preserves the character's position, keeps the untouched ZIP authoritative,
and permits valid PostgreSQL storage. A synthetic regression test covers this real-corpus shape.
Only the four missing chunks were then imported; already completed chunks short-circuited.

## Final canonical state

- 1,557 distinct raw-byte source-file identities across 1,724 source locations;
- 1,760 completed import runs and no other run status;
- 1,187 conversations and exactly 1,187 conversation versions;
- 25,510 events and exactly 25,510 event versions;
- 1,135 records and exactly 1,135 record versions;
- 2,639 assets and exactly 2,639 asset versions and observations;
- 27,602 relationships and exactly 27,602 relationship versions;
- 267,007,679 bytes in the final PostgreSQL acceptance database;
- zero failures, unknowns, parse warnings, or conflicts.

The 2,639 assets are the 1,690 exported external assets plus 949 distinct unavailable provider
references. The difference between 1,724 locations and 1,557 source identities is expected
content-addressed deduplication of byte-identical retained members.

## Exact unchanged replay

The complete retained source was replayed without modification after the NUL repair:

- all 1,724 physical members processed and all 1,724 short-circuited;
- all 1,760 completed run/chunk identities were reused;
- all canonical and version counts remained unchanged;
- complete accounting and `production_ready` were true;
- zero failures, unknowns, warnings, or conflicts;
- exit code 0;
- replay importer time was 23.19 seconds.

## Verification

- Complete PostgreSQL 18/pgvector-backed suite: 242 passed.
- Focused OpenAI production suite: 16 passed.
- Ruff lint: clean for the whole repository.
- Ruff formatting: clean for every changed Python file.
- Python compilation: clean.
- Controlled interruption/resume: passed.
- Targeted repair of the four failed conversation chunks: passed.
- Exact unchanged replay: passed.

## Boundary and next frontier

This acceptance covers the retained official manifest-based export. The legacy standalone
`conversations.json` path remains for compatibility but does not claim whole-export completeness.
Formats absent from the retained export are not claimed.

The next source-parity frontier is Anthropic: add complete inventory and asset disposition,
restart-safe bounded transactions, relationship coverage, and fresh real-corpus acceptance before
building unified search and derived understanding layers.
