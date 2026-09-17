# Official Apple privacy-export ingestion specification

## Scope and provenance boundary

This importer accepts untouched ZIP archives delivered by Apple's privacy portal. Its source is
`apple`, acquisition method is `apple_privacy_export`, and its snapshot scopes begin with
`account/apple_privacy/`.

Independently acquired iPhone, iMazing, RSMF, CSV, or Messages database exports are not inputs.
They remain a separate future source family with device-extraction provenance. A provider product
name never overrides acquisition provenance.

## Source safety

- ZIPs are opened read-only and are never extracted over the source directory.
- Every archive path is checked for absolute paths, drive prefixes, NULs, and parent traversal.
- Nested ZIPs are traversed recursively to a bounded depth using spooled temporary files.
- Duplicate names in one ZIP are addressed by central-directory index, not by ambiguous name.
- Binary bytes stay in the source archive; PostgreSQL stores only an external asset reference,
  content hash, byte count, media type, and provenance.
- Progress and default inventory output use opaque member IDs. `inspect-apple --include-paths` is
  an explicit opt-in because Notes filenames can contain private note titles or content.

## Accounting contract

`inspect-apple` accounts for every recursively observed entry in exactly one disposition:

1. `classified`: a data leaf with one deterministic adapter;
2. `structural`: a directory, nested ZIP container, or provider guide;
3. `unknown`: unsafe or unsupported content that prevents a successful production gate.

The report separates physical source bytes, recursive uncompressed bytes (including nested
containers), and logical leaf bytes (excluding container bytes). Nested compression is visible
without being double-counted as physical source data. Complete accounting requires
`classified + structural + unknown == recursive entries` and exact recursive and leaf byte sums.

## Semantic contract

The permanent registry contains the 32 Apple surfaces observed in the retained export. Present
surfaces route to one of these representations:

- CSV: bounded 5,000-row chunks with all headers, values, and surplus columns retained;
- JSON: arrays, objects, and Apple's observed concatenated top-level values;
- XML: lossless element, attribute, text, and child structure;
- VCF: contacts and groups with provider properties retained;
- ICS: events and reminders with recurrence definitions retained but never expanded;
- Notes text: exact-entry note records linked to stable directory containers; sibling media
  become external assets linked to those containers without guessing among multiple text files;
- other text: durable product records;
- audio, video, and images: streamed, content-addressed external assets.

Every normalized object uses source `apple`. Provider fields remain under `metadata.apple`; PDL
routing and acquisition facts remain under `metadata._pdl`. Durable namespaces include product,
dataset, and object kind so identifiers reused across Apple surfaces cannot collide. Provider IDs
are preferred where present; otherwise identity is a deterministic complete-payload digest with
stable occurrence suffixes for exact duplicates.

Apple account, Store, Music, Wallet, notification, and analytics rows that contain event/history
evidence and a provider time produce both a durable record and a normalized event. This retains
the provider object while making activity queryable. Unknown time formats remain in provider
metadata rather than being guessed.

## Restart and reconciliation

`import-apple-all` inventories before importing. Each data member, or each bounded chunk of a
large CSV, commits independently through the cumulative lossless reconciliation layer. A process
interruption preserves completed transactions; a rerun reuses them. Exact source/parser/category
runs short-circuit. Absence never creates deletion, and conflicts return exit code 2 without
discarding either representation.

Exit codes are `0` for success, `2` for conflicts, `4` for failed classified members, and `5`
for unknown members. Failure takes precedence over unknown, which takes precedence over conflict.

## Production acceptance gate

Production-ready means all of the following are true for the retained corpus:

1. every recursive entry and byte is accounted;
2. unknown members and parse failures are zero;
3. all present products are fully structured;
4. real-data parsing emits no undisposed warning;
5. a controlled interruption resumes without losing completed work;
6. a complete unchanged rerun short-circuits every data member and creates no new versions;
7. the complete PostgreSQL suite, unit suite, Ruff, and Python compilation pass;
8. no source payload, source path, generated inventory, log, or database is committed.
