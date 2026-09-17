# PR14 official Apple privacy-export production acceptance report

## Scope

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

## Boundary and next frontier

Official Apple privacy-export ingestion is production-ready for the retained export. Device-level
iMessage ingestion remains separate and must preserve phone/device extraction provenance. The next
cross-provider phase is a unified query/search layer, followed by evidence-backed identity
resolution and derived claims; none of those layers may rewrite source truth.
