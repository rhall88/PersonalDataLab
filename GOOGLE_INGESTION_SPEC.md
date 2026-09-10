# Google My Activity ingestion specification

Status: implementation contract for `feature/google-ingestion`  
Target release: PersonalDataLab `0.4.0`  
Depends on: the cumulative, lossless canonical storage contract merged in PR3  
Parser contract: `google-my-activity-v1`

## 1. Objective

Add a first-party Google Takeout adapter that imports Google My Activity records into the
existing cumulative canonical store without weakening PR3's identity, history, authority,
or provenance guarantees.

The adapter must make the common activity history useful immediately while remaining narrow.
It must not imply that an entire Google Takeout has been ingested when only My Activity files
were recognized. Unsupported Takeout products remain untouched raw evidence for later,
product-specific adapters.

The implementation is successful when a user can point one command at a standalone activity
file, a Takeout ZIP, a directory of split Takeout ZIPs, or an extracted Takeout tree and receive
deterministic, repeatable, queryable activity events with exact source-file provenance.

## 2. Inherited permanent contract

Google ingestion must obey the storage contract established by PR3:

- canonical logical identity is `(source, source_record_namespace, source_record_id)`;
- raw source identity is `(source, sha256)` and paths are location provenance;
- every distinct normalized fingerprint is retained exactly once;
- repeated identical content reuses its existing version;
- absence from a later export never deletes, tombstones, or revises an older record;
- only an explicit provider deletion may change lifecycle state to `provider_deleted`;
- stale and conflicting distinct versions are retained even when canonical content is unchanged;
- canonical selection uses provider or snapshot authority, never local ingestion order;
- authority timestamps are provenance, not canonical content;
- different fingerprint algorithm versions are never compared;
- every successful observation points to the exact normalized version seen in one run and file;
- exact repeated runs short-circuit while still recording a newly observed filesystem location.

This branch adds no schema migration. It uses PR3's `source_files`, `source_file_locations`,
`import_runs`, `import_run_source_files`, `events`, `event_versions`, and
`event_observations` structures.

## 3. Supported scope

### 3.1 Supported data

The v1 adapter supports activity-card exports commonly produced for:

- Google Search;
- YouTube and YouTube Music activity;
- Google Maps activity;
- Chrome activity;
- Google Play activity;
- Google Shopping activity;
- Google Ads or My Ad Center activity;
- generic My Activity records whose product cannot be classified safely.

Supported representations are:

- `MyActivity.json`;
- `MyActivity.html`;
- YouTube `watch-history.json` or `.html`;
- product `search-history.json` or `.html`.

Filename matching is case-insensitive and ignores punctuation, spaces, underscores, and hyphens.

### 3.2 Explicit non-goals

The following require separate adapters and are not silently treated as imported:

- Gmail MBOX;
- Calendar ICS;
- Contacts vCard or CSV;
- Drive files or native Google document extraction;
- Google Photos binaries or JSON sidecars;
- raw or semantic Location History tracks;
- Fit, Home, Voice, Keep, Tasks, or other product-specific formats;
- media downloading or URL fetching;
- browser-history databases outside Takeout;
- cross-format semantic deduplication between JSON and HTML;
- locale-sensitive parsing of human-readable HTML dates;
- embeddings, semantic search, MCP servers, or agent workflows;
- zip-bomb policy, quotas, or configurable resource limits;
- true record-at-a-time database reconciliation.

Unsupported files stay in the user's immutable Takeout and produce no canonical rows.

## 4. Accepted input shapes and discovery

The CLI accepts exactly one `source` argument with one of these shapes:

1. A standalone supported JSON or HTML activity file.
2. A single ZIP containing one or more supported activity members.
3. A directory containing standard split archives named `takeout-*.zip`.
4. An extracted Takeout tree containing `Takeout/My Activity/...`.
5. A directly selected `My Activity` or YouTube `history` directory.

Discovery requirements:

- resolve the supplied path strictly before parsing;
- accept a deliberately selected standalone file by its supported basename;
- never extract ZIP contents to disk;
- inspect only the ZIP central directory, then open recognized members as streams;
- require ZIP members to appear under a recognized `My Activity` or product `history` hierarchy;
- ignore directories and unsupported members;
- process archive files and members in deterministic case-insensitive filename order;
- prefer standard split archives when a directory contains them;
- raise `GoogleExportError` when no supported activity source is found;
- raise `GoogleExportError` for an invalid ZIP or unreadable recognized member;
- do not claim success merely because an unsupported Takeout was readable.

Each recognized ZIP member or extracted file becomes one `ParsedExport`. A multi-file Takeout is
therefore a deterministic sequence of independently reconcilable source units.

## 5. Raw provenance and run identity

### 5.1 Source-file identity

For an extracted or standalone file, hash the file's exact bytes. For a ZIP member, hash the
member's exact uncompressed bytes while reading it. The containing ZIP is immutable user evidence,
but the database source-file row represents the supported member actually parsed.

`ParsedExport` fields must be populated as follows:

| Field | Required value |
| --- | --- |
| `source` | `google` |
| `parser_version` | `google-my-activity-v1` |
| `category` | deterministic parsing context described below |
| `filename` | relative member name or selected filename |
| `original_path` | absolute file path, or `absolute.zip!/member/path` |
| `sha256` | lowercase SHA-256 of the parsed member bytes |
| `size_bytes` | uncompressed byte size of that member or file |
| `snapshot_kind` | `partial` |
| `snapshot_scope` | `account/activity` |
| `exported_at` | Takeout timestamp when safely inferred, otherwise null |
| `export_id` | normalized Takeout filename timestamp when present |

### 5.2 Parsing category

The PR3 run key deliberately excludes filenames because names are location provenance. Google
normalization can nevertheless depend on the member format and semantic product context. The
adapter must therefore populate an explicit category:

```text
my_activity/<normalized-product-or-generic>/<json-or-html>
```

Examples:

```text
my_activity/youtube/json
my_activity/maps/html
my_activity/generic/json
```

Product context is one of the canonical keys `youtube`, `maps`, `search`, `chrome`, `shopping`,
`play`, or `ads`; unrecognized context becomes `generic`. This category is included in run
identity and stored in `import_run_source_files.category`. Renaming identical bytes without
changing semantic context must short-circuit. Equivalent labels such as `Maps` and `Google Maps`
must produce the same category. Reusing identical bytes under a genuinely different semantic
product context must be parsed as a distinct run rather than incorrectly reusing the earlier
result. Selecting a file directly or discovering that same file from an ancestor directory must
produce identical category and canonical event semantics.

### 5.3 Takeout export identity

Infer export authority only from an entire path component matching one of:

```text
takeout-YYYYMMDDTHHMMSSZ
takeout-YYYYMMDDTHHMMSSZ-NNN
takeout-YYYYMMDDTHHMMSSZ.zip
takeout-YYYYMMDDTHHMMSSZ-NNN.zip
```

The timestamp is UTC. Split suffixes such as `-001.zip` do not affect the export ID. If no valid
timestamp is present, both `exported_at` and `export_id` are null. Filesystem modification times
must never be used as authority. Incidental substrings inside components such as
`backup-of-takeout-YYYYMMDDTHHMMSSZ-old` must not confer authority.

## 6. JSON parsing contract

JSON input must:

- be UTF-8, optionally with a BOM;
- contain one top-level array;
- contain only JSON whitespace after the closing array;
- be decoded incrementally in fixed-size byte chunks;
- be hashed over every byte while decoding;
- reject malformed separators, truncated values, invalid UTF-8, or trailing content;
- reject any array item that is not an object;
- reject objects lacking all recognized anchor fields: `title`, `time`, `header`, and `products`.

The parser must not call `json.load` on an entire activity file. Its incremental decoder may retain
one unusually large JSON value in memory. The current database API still materializes all
normalized events for one supported member before reconciliation; see section 11.

## 7. HTML fallback contract

HTML is a best-effort fallback for Takeout activity cards using the standard
`div.outer-cell` structure.

For each card:

- preserve visible text in document order;
- preserve unique hyperlink targets in document order;
- use the first visible line as a header when multiple lines exist;
- use remaining visible lines as title text;
- use the product/path hint as fallback classification evidence for a one-line card;
- set the first link as `titleUrl` and retain the full unique link list;
- reject a recognized HTML file containing no recognizable cards.

HTML dates are localized and structurally inconsistent. V1 must leave `occurred_at` null rather
than guess. Date-like visible text remains canonical card text. HTML-derived identities are not
expected to match identities from JSON exports of the same human activity.

## 8. Normalized event contract

Every accepted activity record becomes one `NormalizedEvent`:

| Field | Rule |
| --- | --- |
| `source` | `google` |
| `source_record_namespace` | `activity` |
| `source_record_id` | deterministic digest identity from section 9 |
| `actor_identity` | `account_owner` |
| `occurred_at` | parsed provider `time`, normalized to UTC, or null |
| `event_type` | deterministic product classification below |
| `text` | normalized title/subtitle/description projection |
| `metadata.google` | provider fields except successfully parsed `time`, plus safe adapter context |
| `provider_updated_at` | null; My Activity does not expose one |
| `authority_at` | inferred Takeout export timestamp or null |
| `authority_kind` | `exported_at` when known, otherwise `unknown` |
| `lifecycle_state` | `active` |

### 8.1 Timestamp parsing

Accept:

- ISO-8601 strings, including `Z` and explicit offsets;
- numeric Unix seconds;
- numeric Unix milliseconds when the magnitude is at least `100000000000`.

Every timezone-aware result must be converted to UTC. A timezone-naive ISO value is interpreted as
UTC. Booleans, empty strings, non-finite numbers, invalid dates, and out-of-range values are not
timestamps. If a non-null raw `time` cannot be parsed, set `occurred_at` to null and preserve the
original value as `metadata._pdl.unparsed_time`.

### 8.2 Event classification

Classification is deterministic and case-insensitive. Provider evidence from `header`, `products`,
and `activityControls` is authoritative. Canonical product context derived from a recognized path
structure or history filename is consulted only when those fields do not identify a supported
product. Matching uses normalized words and explicit phrases, not arbitrary substrings. Keyword
precedence within either evidence tier is:

1. YouTube -> `youtube_activity`
2. Maps -> `maps_activity`
3. Search -> `search_activity`
4. Chrome -> `browser_activity`
5. Shopping -> `shopping_activity`
6. Play -> `play_activity`
7. Ad Center, Google Ads, or My Ad -> `ad_activity`
8. otherwise -> `google_activity`

When canonical path context is needed as fallback classification evidence, its semantic key is
retained as `metadata._pdl.takeout_product`. Raw folder labels remain only in `original_path` and
`filename`; they never enter canonical metadata. Because semantic context can affect normalization,
it is also represented by the parsing category. A folder name must not override self-identifying
provider fields or cause path-only canonical-content churn.

### 8.3 Canonical text

Canonical text is built from `title`, `subtitles`, and `description`, in that order.

- recursively flatten strings contained in lists and objects;
- traverse object values in sorted-key order so key insertion order cannot change canonical text;
- collapse internal whitespace in each string;
- remove empty strings;
- preserve first occurrence order;
- remove exact duplicate strings;
- join remaining values with newlines;
- use null when no visible text remains.

### 8.4 Provider metadata

Copy every JSON provider field to `metadata.google` except `time`, which is represented by
`occurred_at` when valid. If `time` is unparseable, preserve its original value as
`metadata._pdl.unparsed_time`. Adapter-owned metadata lives under the reserved top-level `_pdl`
namespace, never among provider-owned keys under `metadata.google`. URLs, subtitles, products,
activity controls, and unrecognized fields remain available unchanged. The adapter must never
dereference URLs.

## 9. Logical identity and duplicates

Google My Activity does not expose a durable record identifier. V1 defines identity as:

```text
activity:<sha256(canonical-json(provider-record))>[:occurrence]
```

Rules:

- JSON object-key ordering must not affect the digest;
- all provider fields, including raw `time`, participate in identity;
- the first exact record occurrence has no suffix;
- later identical occurrences in the same source member use `:2`, `:3`, and so on;
- occurrence numbering follows source order and is deterministic;
- unchanged records in later exports receive the same identity;
- a changed provider record receives a new identity because Google supplies no safe revision key;
- missing records in a later export remain in the cumulative corpus;
- semantically similar but byte-distinct records are not heuristically merged;
- JSON and HTML records are not cross-deduplicated.

The digest identifies an immutable provider observation, not a mutable Google entity. This is a
deliberate conservative choice: false duplicates are preferable to merging unrelated activities
or inventing revision history unsupported by provider identifiers.

## 10. Authority and reconciliation

Each My Activity member is a partial account/activity snapshot. Takeout export time is the only
available authority signal and is excluded from the canonical fingerprint.

Consequences:

- importing identical member content from a later Takeout reuses all versions;
- the later observation may advance canonical authority watermarks without revision churn;
- importing older identical content later cannot regress authority;
- changed raw records normally have new logical IDs and coexist cumulatively;
- no absence-based tombstones are synthesized;
- ambiguous collisions are retained as conflicted versions and cause CLI exit code `2`;
- unknown-authority imports remain valid but cannot displace different known-authority content.

## 11. Execution, transaction, and memory model

The CLI consumes `iter_google_exports` serially. It imports each recognized member in its own
database transaction, releases its event payload, then advances to the next member.

Required behavior:

- one malformed member causes no database mutation for that member;
- members committed earlier in the command remain committed;
- rerunning after correction short-circuits completed members and processes the corrected member;
- the CLI retains only compact per-member summaries after each commit;
- source/member order is deterministic;
- no supported member is silently skipped after discovery;
- reconciliation counts must match parsed counts before commit.

Memory guarantee for v1:

- ZIP archives and complete Takeout directory sets are never loaded wholesale;
- JSON bytes are decoded incrementally;
- processing is serial by supported member;
- peak normalized-event memory is proportional to the largest supported member, not the complete
  multi-archive Takeout.

True O(single-record) database ingestion is not promised in v1 because PR3 validates logical-ID
uniqueness and reconciles one complete `ParsedExport` atomically. Achieving it requires a staged
streaming import API with equivalent duplicate detection, run identity, rollback, and count
validation; that architectural change is deferred rather than faked in this branch.

## 12. CLI contract

Command:

```powershell
personal-data-lab import-google <source> [--database-url URL]
```

The command must:

- use the standard database URL resolution used by other adapters;
- parse and import members serially;
- print one final JSON summary after complete success;
- include compact per-file provenance and reconciliation statistics;
- include aggregate source/database counts, version counts, conflict counts, and short-circuit state;
- return `0` when all members reconcile without conflicts;
- return `2` when any committed member contains ambiguous conflicts;
- propagate parsing, migration, connection, and reconciliation failures as non-zero errors.

If a later member fails, the command may terminate before printing the final aggregate summary.
Previously committed member runs remain inspectable in the database and safe to resume.

## 13. Safety properties

- Never modify, rename, extract over, or delete the source export.
- Never execute HTML or JavaScript.
- Never fetch provider URLs or linked media.
- Never use filesystem timestamps as provider authority.
- Never infer authority from a partial path-component match.
- Never accept an unsupported product as successfully imported.
- Never allow invocation path shape or raw directory labels to alter canonical event content.
- Never silently drop an invalid record from a recognized member.
- Never infer deletion from absence.
- Never include Takeout export time in canonical content fingerprints.
- Never compare fingerprints produced by different fingerprint versions.

## 14. Required test matrix

### Discovery and provenance

- standalone JSON;
- standalone HTML;
- single ZIP with multiple product members;
- directory of split ZIPs in deterministic order;
- extracted Takeout tree;
- directly selected activity/history directory;
- unsupported tree rejection;
- supported basenames outside recognized ZIP activity hierarchies are ignored;
- invalid ZIP rejection;
- exact member SHA and uncompressed size;
- inferred Takeout export timestamp and ID;
- category distinguishes format/product context but not filename/path changes.
- exact-file and ancestor-directory invocation produce identical category, identity, and content;
- equivalent product folder labels normalize to one semantic context key;
- incidental Takeout-like path substrings do not confer export authority.

### JSON and normalization

- input larger than one read buffer;
- a single JSON record larger than one read buffer;
- UTF-8 BOM;
- invalid UTF-8;
- malformed/truncated array;
- missing comma and trailing content;
- non-object record;
- object with no recognized fields;
- ISO `Z`, explicit-offset, numeric seconds, and numeric milliseconds timestamps;
- invalid timestamp preservation;
- classification for every supported product and generic fallback;
- adversarial product words do not produce substring-based misclassification;
- recursive text flattening, whitespace normalization, and deduplication;
- provider-field retention, parsed-time removal, and collision-proof adapter metadata.

### HTML

- multiple cards;
- nested divs and line breaks;
- duplicate links;
- one-line product fallback;
- no-card rejection;
- no localized-date guessing.

### Identity and reconciliation

- object key order does not change identity;
- nested object key order does not change canonical text or content fingerprints;
- exact duplicate occurrences receive stable distinct suffixes;
- later export with old plus new records reuses old versions and inserts only new records;
- later absence retains earlier canonical records;
- timestamp/export-authority-only changes do not create content versions;
- same source bytes at a new path/name short-circuit;
- same bytes under different parsing category do not false-short-circuit;
- integration counts and observation/version pointers reconcile exactly;
- conflicts produce exit code `2`.

## 15. Implementation checklist

Before merge, the branch must:

1. Include merged PR3, including its provider-timestamp and filename/run-key fixes.
2. Populate Google parsing categories and persist them through run provenance.
3. Normalize explicit-offset timestamps to UTC.
4. Document the actual per-member memory boundary without claiming record-at-a-time ingestion.
5. Cover the required high-risk cases with focused tests.
6. Pass the complete unit and disposable-PostgreSQL integration suite.
7. Pass Ruff, Python compilation, whitespace checks, and package build.
8. Contain no raw personal Takeout data, credentials, extracted media, or generated database data.

## 16. Definition of done

The Google branch is complete when every in-scope requirement above is implemented, all required
tests pass, the branch is based on merged PR3, documentation and code describe the same guarantees,
and review finds no unresolved correctness issue in discovery, identity, authority, provenance,
failure atomicity, or cumulative reconciliation.
