# PR19 Anthropic production acceptance report

## Outcome

Anthropic parser version `anthropic-v2` is production-ready for the retained official account
export. A fresh privacy-safe inventory, isolated PostgreSQL import, controlled interruption,
resume, and exact unchanged replay completed on 2026-09-18 with exit code 0. No source files or
members failed, no undeclared files or parse warnings remained, and no canonical conflicts were
created.

The historical `anthropic-v1` parser remains a compatibility API only. Its completed runs are not
treated as evidence for this acceptance.

## Retained-source preparation

The retained download had one provider-declared archive stored one directory above the manifest
and a previously generated normalized ZIP beside the provider files. The originals were not moved,
renamed, or modified. Acceptance used a git-ignored source view containing byte-for-byte copies of
only the manifest and the five ZIPs it declared. Every copied archive and the manifest were verified
against the retained source with SHA-256 before inventory.

The normalized ZIP was excluded because it is derived data and is not named by the provider
manifest. This staging repair changed acquisition layout only, not source bytes or provider data.

## Inventory evidence

| Measure | Accepted count |
|---|---:|
| Physical source files | 6 |
| Manifest files | 1 |
| Declared archives | 5 |
| Undeclared archives | 0 |
| Archive members | 8 |
| Structured JSON members | 8 |
| Binary asset members | 0 |
| Physical source bytes | 9,534,108 |

The five observed categories were all present: `light_metadata`, `projects`, `memories`,
`design_chats`, and `conversations`. Default inventory and progress output exposed only aggregate
counts and opaque content-derived identifiers.

## Controlled interruption and resume

Acceptance used a fresh, dedicated PostgreSQL 18/pgvector database initialized at schema version 7.
The first run was intentionally interrupted immediately after the first 25-conversation chunk had
committed. The durable partial state was:

| Measure | Count after interruption |
|---|---:|
| Source files | 6 |
| Completed import runs | 8 |
| Conversations | 26 |
| Events | 608 |
| Records | 1 |
| Assets | 0 |
| Relationships | 506 |

The 26 conversations comprise one design chat plus the committed 25-conversation ordinary chunk.
Rerunning the importer reused the five already completed source-file groups and continued with the
remaining conversation chunks.

## Complete retained-corpus reconciliation

| Measure | Accepted count |
|---|---:|
| Source files processed | 6 |
| Source files failed | 0 |
| Members processed | 8 |
| Members failed | 0 |
| Bounded import units | 18 |
| Conversations | 269 |
| Events | 9,748 |
| Records | 1 |
| Assets | 601 |
| Available assets | 0 |
| Unavailable assets | 601 |
| Relationships | 10,014 |
| Parent relationships | 9,405 |
| Attachment/file relationships | 607 |
| Conflicts | 0 |
| Parse warnings | 0 |
| Exit code | 0 |

Category reconciliation was:

| Category | Conversations | Events | Records | Assets | Relationships |
|---|---:|---:|---:|---:|---:|
| Manifest | 0 | 0 | 1 | 0 | 0 |
| Light metadata | 0 | 60 | 0 | 0 | 0 |
| Projects | 0 | 4 | 0 | 0 | 1 |
| Memories | 0 | 11 | 0 | 0 | 0 |
| Design chats | 1 | 4 | 0 | 0 | 1 |
| Conversations | 268 | 9,669 | 0 | 601 | 10,012 |

The retained archives contain no binary members. All 601 attachment/file objects are therefore
honest unavailable-asset placeholders backed by provider references; the importer did not invent
paths, guess filename matches, or fetch remote content.

## Exact replay and version stability

An unchanged replay short-circuited all six source-file groups and all 18 bounded units. Counts
before and after replay were identical:

| Durable table family | Before | After |
|---|---:|---:|
| Source files | 6 | 6 |
| Import runs | 18 | 18 |
| Conversation versions / observations | 269 / 269 | 269 / 269 |
| Event versions / observations | 9,748 / 9,748 | 9,748 / 9,748 |
| Record versions / observations | 1 / 1 | 1 / 1 |
| Asset versions / observations | 601 / 601 | 601 / 601 |
| Relationship versions / observations | 10,014 / 10,014 | 10,014 / 10,014 |

## Regression gate

- all 249 repository tests passed against PostgreSQL 18/pgvector;
- the guarded Anthropic integration test covers committed interruption, resume, exact replay, and
  stable version counts;
- repository-wide Ruff linting and Python byte-compilation passed;
- the dependency lock and Git whitespace checks passed; and
- the six newly added Anthropic modules/tests are formatter-clean.

## Accepted production boundary

The accepted boundary is the retained official manifest and its five declared archive categories.
Signed download URLs are not called, logged, or persisted. Binary bytes remain outside PostgreSQL.
Future undeclared archives, schema drift, invalid members, warnings, failures, or conflicts continue
to block the production-ready gate instead of being silently accepted.
