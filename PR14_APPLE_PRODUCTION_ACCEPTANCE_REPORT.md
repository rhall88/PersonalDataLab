# PR14 official Apple privacy-export production acceptance report

## Current status: parser v3 accepted in PR16

Fresh parser-v3 real-corpus persistence, controlled interruption/resume, semantic reconciliation,
the empty-TestFlight repair, and exact unchanged replay are recorded in
[`PR16_APPLE_V3_PRODUCTION_ACCEPTANCE_REPORT.md`](PR16_APPLE_V3_PRODUCTION_ACCEPTANCE_REPORT.md).
The remainder of this report preserves the historical parser-v2 evidence and the review gate that
PR16 has now completed.

The corpus results below are historical evidence for parser v2 at `c4bfb4c`, not acceptance of the
parser-v3 implementation by themselves. Review reproduced nine classes of
correctness/provenance/privacy defects despite
that successful exact replay. Parser v3 fixes those cases and adds synthetic regression coverage.

Changes include context-aware replay categories, packaging-independent canonical metadata,
conservative dataset identity policies, parameter-preserving VCF/ICS and timezone interpretation,
complete XML trees and positional duplicate CSV headers, validated component boundaries, precise
PDF-guide classification, indexed nested locators, and default source-path redaction. Coverage now
reports generic structured adapters as partial and refuses production-ready status on warnings.

Required before claiming full production acceptance again:

1. Run v3 against the retained read-only corpus in a fresh isolated PostgreSQL database.
2. Reconcile the newly indexed PDFs, corrected XML/calendar representations, and all note and
   attachment contexts. Reassess the four duplicate-byte run reuses below individually.
3. Disposition generic structured coverage with product-specific semantic fixtures/adapters;
   exit 3 is an explicit partial-support result, not a failed persistence operation.
4. Repeat interruption/resume and exact replay; record sanitized canonical/version/run counts.
5. Keep the v2 database as historical evidence. A parser-version bump does not erase old identities.

The private corpus is unavailable in the review workspace, so no new real-data counts are claimed.

## Review-fix regression evidence

- Local suite: 183 passed; 39 PostgreSQL-dependent tests skipped (no local database server).
- Focused Apple suite: 33 passed, including the reproduced review failures.
- Four additional PostgreSQL regressions cover identical notes/shared attachments, changed and
  smaller exports, repackaged-source replay/provenance, and CSV interruption/resume/replay.
- Ruff, Python compilation, and diff whitespace checks passed locally.
- PostgreSQL 18/pgvector CI at `7cbc82b` passed all 222 tests, including all four new database
  regressions, with no skipped tests. Ruff, compilation, and whitespace checks also passed.
  Evidence: https://github.com/rhall88/PersonalDataLab/actions/runs/35247146201/job/105290126970
- The stale project version in `uv.lock` was synchronized with pyproject.toml (0.13.0);
  `uv lock --check --offline` passes. These synthetic tests do not replace corpus acceptance.

## Historical parser v2 scope

PR14 brings the retained official Apple privacy export to the same operational standard as the
Google importer: recursive accounting, explicit product routing, semantic normalization,
external-asset indexing, restart-safe transactions, exact unchanged replay, and a complete
PostgreSQL regression gate.

Raw Apple archives remained read-only. No source payload, source path, generated inventory,
runtime log, temporary script, or database is committed. Independently extracted iMessages are
not part of this importer and remain a separate acquisition/provenance family.

## Corpus accounting

The retained export contains:

- 31 outer ZIP archives;
- 1,503,886,835 physical source bytes;
- 832 recursively observed entries;
- 18 nested ZIP containers;
- 347 provider guide PDFs;
- 467 classified data leaves;
- 365 structural entries in total;
- zero unsafe or unknown members;
- 1,960,490,082 logical leaf bytes, including provider guides;
- 1,916,888,949 classified data bytes processed.

Physical source size and logical leaf size are intentionally separate. Nested container bytes are
not double-counted as physical source data.

The 32-surface registry has 29 distinct present routing groups and three registered groups not
present as distinct leaves in this export generation. Every present group is fully structured.

## Representations

- CSV is streamed in bounded 5,000-row chunks with surplus columns retained.
- JSON supports arrays, objects, and Apple's concatenated top-level-value variant.
- XML retains elements, attributes, text, and child structure, including mislabeled encoding
  declarations observed in the export.
- VCF becomes Contacts/groups; ICS becomes Calendar events/reminders without expanding recurrence.
- Notes become exact-entry records linked to stable note-directory containers.
- Note media and Voice Memos remain external, content-addressed assets.
- Duplicate names in a nested ZIP are addressed by central-directory index and retained as
  distinct assets when their bytes differ.

No schema migration was required. The universal record, event, asset, relationship, version, and
observation tables introduced through schema version 7 already express Apple provenance.

## Restart proof

A controlled interruption after 12 completed or partially completed members left these durable
counts in an isolated PostgreSQL test database:

- 12 source files and 12 completed import runs;
- 2,112 records and 2,112 record versions;
- 99 events and 99 event versions.

Resuming reused the completed prefix and continued from source. This proves that completed member
or CSV-chunk transactions survive process interruption.

## Production defect found and fixed

The first complete production-shaped pass found 10 Notes conflicts. The export contained 149 text
entries in 135 note directories, including five directories with multiple text files. The first
identity model used only the directory and therefore collapsed distinct note entries.

Parser v2 now creates:

1. one stable directory container per note directory;
2. one durable note per exact text entry;
3. an explicit container-to-note relationship;
4. container-to-asset relationships for sibling media.

This avoids guessing which note owns an attachment when a directory contains multiple text files.
A targeted real-data replay of all 163 Notes members produced zero conflicts before the clean
acceptance database was built.

## Clean complete pass

The corrected from-scratch import completed in 3,682 seconds and produced:

- 467/467 imported data members in 550 bounded parser chunks;
- zero failed members, unknown members, parse warnings, or conflicts;
- 458 distinct source-byte identities and 546 distinct import runs after exact-byte reuse;
- 476,439 canonical records and exactly 476,439 record versions;
- 162,318 canonical events and exactly 162,318 event versions;
- 33 external assets and exactly 33 asset versions;
- 156 relationships and exactly 156 relationship versions;
- 3,804,010,175 bytes in the final PostgreSQL acceptance database.

Repeated stable note containers account for 19 unchanged record observations during the first
clean pass. Four duplicate-byte members legitimately reused completed runs. Neither case created
an alternate version.

## Exact unchanged replay

The complete corrected source was replayed without modification:

- all 467 data members short-circuited;
- all 550 parser chunks matched completed runs;
- exit code 0 and `production_ready: true`;
- zero conflicts, failures, unknown members, or warnings;
- canonical and version counts remained exactly unchanged.

The replay updates ordinary source `last_seen_at` provenance, but creates no canonical object,
version, or import run.

## Verification

- Complete test suite with disposable PostgreSQL: 197 passed.
- Focused Apple suite: 12 passed.
- Real-data parse-only pass: 467/467 members, zero failures and warnings.
- Ruff lint: clean for the whole repository.
- Ruff formatting: clean for every file changed by PR14; unrelated pre-existing formatting is
  intentionally untouched.
- Python compilation: clean.
- Clean complete import: exit 0.
- Exact unchanged replay: exit 0, 467/467 members short-circuited.

## Historical v2 conclusion (superseded by the current-status gate above)

Official Apple privacy-export ingestion is production-ready for the retained export. Device-level
iMessage ingestion remains separate and must preserve phone/device extraction provenance. The next
cross-provider phase is a unified query/search layer, followed by evidence-backed identity
resolution and derived claims; none of those layers may rewrite source truth.

