# PersonalDataLab

A local, provenance-first foundation for importing personal data into PostgreSQL.

This repository contains the completed foundation and its source importers:

- Docker Compose
- PostgreSQL 18 plus pgvector
- canonical records (`people`, `identities`, `conversations`, `events`, `attachments`, durable
  records/assets, and provenance-bearing relationships)
- source-file provenance (`source_files`, `source_file_conversations`, and `source_file_events`)
- lossless distinct-version history plus narrow import-run/observation provenance
- cumulative reconciliation for inserts, genuine updates, unchanged overlap, stale data,
  conflicts, explicit deletion, and reactivation
- production-accepted official OpenAI data-export ingestion with manifest accounting, sharded
  conversations, external assets, restart, and exact replay
- Anthropic data-export importer
- Google Takeout My Activity importer
- Google Takeout 68-product inventory, adapter registry, routing, and coverage framework
- Google Timeline/Maps/Saved, Calendar, Contacts, and Tasks semantic adapters
- bounded Gmail MBOX plus Google Chat, Voice, Meet, and Groups communications adapters
- Google Photos/Drive/Recorder rich metadata, document text, and external-asset graph adapters
- Gemini/Flow/NotebookLM/Keep/Journal AI, knowledge, note, and generated-work adapters
- Google account/commerce/device/home adapters, including Play Store and Nest JSONL handling
- Google long-tail semantic adapters, YouTube/Chrome product data, and external My Activity assets
- production acceptance against the retained 146.25 GB Takeout, including restart and targeted
  idempotency evidence
- recursive official Apple privacy-export ingestion with semantic native-format adapters
- independent iMazing Messages CSV ingestion for conversations, participants, messages, replies,
  reactions, edits, deletions, and attachment references
- reconciliation tests
- Windows/PowerShell setup instructions

MCP, embeddings, later source importers, full-text search, analytics, DuckDB/Parquet, identity
resolution automation, and derived claims are deliberately deferred.

## 1. Prerequisites

- Windows with PowerShell
- Docker Desktop using WSL 2
- Python 3.12 or newer
- Git

Verify Docker before continuing:

```powershell
docker --version
docker compose version
docker run --rm hello-world
```

## 2. Configure local secrets

Create a random database password and copy the example environment file:

```powershell
[guid]::NewGuid().ToString("N")
Copy-Item .env.example .env
```

Replace both `replace-with-a-random-value` values in `.env` with the generated value.
The `.env` file is ignored by Git.

## 3. Start PostgreSQL and pgvector

```powershell
docker compose up -d --wait
docker compose ps
docker compose exec db psql -U personaldata_admin -d personaldata -c "SELECT version();"
docker compose exec db psql -U personaldata_admin -d personaldata -c "SELECT extversion FROM pg_extension WHERE extname = 'vector';"
```

The database is bound to `127.0.0.1`, persists in the `postgres_data` Docker volume,
and initializes pgvector plus the schema on the first start. PostgreSQL 18 stores its
versioned data directory beneath the volume mounted at `/var/lib/postgresql`.

## 4. Install the importer

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### Upgrade an existing PR2 database

Initialization SQL is only applied when PostgreSQL creates an empty data volume. Back up an
existing database, install the new importer, and apply the tracked migrations explicitly:

```powershell
personal-data-lab migrate
```

The command is idempotent. It backfills provider namespaces, canonical fingerprints, distinct
version rows, and compact version-backed observations. PR2 memberships become full versions of
the currently available canonical payload. If upgrading an intermediate PR3 database whose
observations contain an older fingerprint but not its reconstructable payload, the migration
retains that evidence as a `fingerprint_only` version instead of pretending the old payload is
known or discarding the fact that it existed.

## 5. Store untouched exports

Keep original provider files unchanged. For example:

```text
data/raw/
├── anthropic/
│   └── 2026-09-09/
│       ├── manifest-....json
│       ├── conversations-000.zip
│       ├── design_chats-000.zip
│       ├── light_metadata-000.zip
│       ├── memories-000.zip
│       └── projects-000.zip
├── openai/
│   └── 2026-09-08/
│       └── openai-export.zip
└── google/
    └── 2026-09-10/
        ├── takeout-20260910T120000Z-001.zip
        └── takeout-20260910T120000Z-002.zip
```

Everything below `data/raw/` is ignored by Git. Do not edit, replace, or unpack over
the original files. The importers read ZIP archives directly and record the immutable
source bytes' SHA-256, path, size, parser version, export time when supplied, and ingestion
time.

## 6. Import OpenAI and reconcile

Inspect the untouched official ZIP (or an extracted copy) before importing it:

```powershell
personal-data-lab inspect-openai data\raw\openai\2026-09-08\openai-export.zip --format table
```

Default JSON and progress output use opaque member IDs. Add `--include-paths` only when private
member paths are acceptable in the output.

```powershell
personal-data-lab import-openai data\raw\openai\2026-09-08\openai-export.zip
```

Official ZIP and directory imports validate the provider manifest, account for every physical
and logical member, and process each asset, structured JSON file, and bounded conversation chunk
in a separate restart-safe transaction. Conversation graph nodes use conversation-scoped
identity, parent edges remain queryable, and every `sediment://` reference becomes a
message-to-asset relationship. Missing provider assets are retained as explicit unavailable
placeholders rather than silently discarded. Binary bytes and rendered HTML stay external;
PostgreSQL stores their hashes, sizes, paths, metadata, and provenance only.

Successful output includes whole-export accounting and semantic totals:

```json
{
  "physical_members_total": 1724,
  "files_processed": 1724,
  "files_failed": 0,
  "conversations": 1187,
  "messages": 25510,
  "production_ready": true,
  "exit_code": 0
}
```

Exact reruns reuse completed source/parser/category identities and create no unintended canonical
objects or versions. The legacy standalone `conversations.json` input remains available through
the original single-file path, but it cannot provide manifest-level completeness or asset
accounting. See [`OPENAI_INGESTION_SPEC.md`](OPENAI_INGESTION_SPEC.md) and
[`PR17_OPENAI_PRODUCTION_ACCEPTANCE_REPORT.md`](PR17_OPENAI_PRODUCTION_ACCEPTANCE_REPORT.md).

## 7. Import Anthropic and reconcile

Place the untouched Anthropic manifest beside every `*-000.zip` file it names, then
pass the local manifest to the importer:

```powershell
personal-data-lab import-anthropic data\raw\anthropic\2026-09-09\manifest-....json
```

The importer supports the `conversations`, `design_chats`, `light_metadata`, `memories`,
and `projects` categories. It reads only the sibling archives named by the manifest. It
does not call, log, or persist the manifest's signed download endpoints, so an expired
endpoint does not matter after the source files have been downloaded.

Each archive becomes its own `source_files` row. All five archives are imported in one
database transaction, and the command reports per-file and total reconciliation. A
failure in any archive rolls back the complete batch.

For ordinary Claude messages, canonical event text comes only from
`content[type=text]` blocks, joined with newlines. Provider content blocks, attachment
metadata, file references, and any differing top-level export text remain in the event's
`metadata.anthropic` object. This slice does not invent attachment filesystem paths or
perform automatic person/identity resolution.

Previously generated normalized ZIPs are useful as independent reconciliation oracles,
but they are derived data and are not importer inputs. The untouched manifest and
`*-000.zip` archives remain the source of truth.

## 8. Import Google My Activity and reconcile

Point the command at one Takeout ZIP, a directory containing all split Takeout ZIPs, an
extracted Takeout directory, or a standalone `MyActivity.json`/`MyActivity.html` file:

```powershell
personal-data-lab import-google data\raw\google\2026-09-10
```

The normative behavior, identity rules, failure model, and acceptance matrix are documented in
[`GOOGLE_INGESTION_SPEC.md`](GOOGLE_INGESTION_SPEC.md).

This first Google slice imports the common My Activity records used by Search, YouTube,
Maps, Chrome, Play, Shopping, and Ads. JSON is preferred because its timestamps are
machine-readable; the HTML fallback preserves activity-card text and links, but leaves
localized dates unset when they cannot be interpreted safely.

The importer scans ZIP central directories and opens only supported activity members. It
does not extract or read photo, video, Drive, or other binary payloads. Each supported member
is hashed while it is parsed and is recorded by immutable byte identity; identical bytes at a
new location reuse the source row and add location provenance. Split archives are processed in
filename order, and each member commits independently so a later malformed member can be
corrected and the command rerun without duplicating completed work.

Member-level parsing and reconciliation progress is written to stderr without event text or
provider payloads. Stdout remains reserved for the final machine-readable JSON summary.

JSON decoding is incremental and Takeout members are processed serially. The current database
contract still materializes the normalized events for one supported member before reconciling
it, so peak memory is bounded by the largest supported member rather than a single record. True
record-at-a-time database ingestion requires a separate staged-import design.

Google does not provide a durable record ID for My Activity rows. The adapter therefore uses
a deterministic digest of the complete provider record, with stable occurrence suffixes for
identical duplicates. Logical identity is scoped by the resolved product and JSON/HTML format;
provider fields determine product when possible, with canonical path context used only as a
fallback. The same unchanged activity receives the same logical identity in a later export.
All provider fields remain available under `metadata.google`; adapter-owned context is isolated
under `metadata._pdl`, and normalized `time` is stored as `occurred_at` instead of being duplicated
in fingerprinted metadata.

Gmail MBOX content, Drive document extraction, Google Photos sidecars and binaries, and observed
Location History variants are handled by the registered semantic and external-asset adapters.
Absent products remain explicitly `not_present_in_export`; no empirical-support claim is made for
formats missing from the retained Takeout.

### Inspect a complete Google Takeout

Inventory one ZIP, a directory of split ZIPs, or an extracted Takeout without extracting ZIPs or
opening obvious member payloads:

```powershell
personal-data-lab inspect-google data\raw\google\2026-09-10
personal-data-lab inspect-google data\raw\google\2026-09-10 --format table
```

JSON is the default. It includes every archive member, ZIP sizes/CRC where available, inferred
export identity, canonical product key, candidate adapter, member disposition, per-product
totals, unknown members, and the complete-accounting invariant. Directory entries and Takeout's
archive-browser files are counted as intentional structural members, never silently dropped.

The permanent 68-product contract is in
[`GOOGLE_TAKEOUT_COMPLETE_INGESTION_SPEC.md`](GOOGLE_TAKEOUT_COMPLETE_INGESTION_SPEC.md), and
current product-by-product status is in
[`GOOGLE_PRODUCT_COVERAGE.md`](GOOGLE_PRODUCT_COVERAGE.md). The implementation architecture is
summarized in
[`GOOGLE_TAKEOUT_FRAMEWORK_ARCHITECTURE.md`](GOOGLE_TAKEOUT_FRAMEWORK_ARCHITECTURE.md).

### Import all currently supported Takeout members

```powershell
personal-data-lab migrate
personal-data-lab import-google-all data\raw\google\2026-09-10
```

`import-google-all` inventories first, routes deterministically, and commits one logical member
at a time. My Activity uses the streaming activity parser. PR6 adds semantic routing for Timeline,
Maps, Maps (your places), My Maps, Saved, Calendar ICS, Contacts VCF/CSV/JSON, and Tasks JSON/CSV.
PR7 adds a bounded two-pass Gmail MBOX stream plus semantic routing for Google Chat, Voice, Meet,
and Groups. Large MBOX members are committed in deterministic chunks while member coverage and
byte accounting remain exact.
PR8 adds Photos sidecar/album semantics, Drive structured-text and Office Open XML extraction,
Recorder transcript/audio links, and external asset indexing for Photos, Drive, Recorder, Phone
Audio, and Street View. Binary bytes remain in their immutable source files, never PostgreSQL.
PR9 adds AI project, generated-media, conversation, notebook, document, and note semantics for
Gemini, Gemini in Workspace, NotebookLM, Flow, Workspace Studio, Keep, Journal, Pinpoint, and
Google Cloud Search, including explicit NotebookLM and Flow relationships.
PR10 adds account, subscription, payment, purchase, device, home, and provider-event semantics
across Google Account, One, Pay, Wallet, Purchases & Reservations, Shopping, Store, Play Store,
Finance, Fi Wireless, Pixel, Android Device Configuration, Home App, Nest, and Profile.
PR11 adds semantic records/events and streamed external assets for the remaining 25-product long
tail: account access, notifications, education, publishing, Chrome, legal exports, business,
developer, Earth, feedback, health, entertainment, Marketplace, Tenor, and non-My-Activity
YouTube. It also indexes provider-supplied My Activity attachments while leaving activity HTML
and JSON on the original streaming parser.
Provider-native fields remain lossless, recurring calendar definitions are not expanded, and
explicit list/map/calendar/contact/task relationships are versioned with their own provenance.
Other bounded JSON/CSV/XML members may use the conservative generic fallback and are reported as
`partially_structured`; unimplemented variants remain `raw_preserved`. Progress goes to stderr
and final coverage JSON goes to stdout.

Exit codes are exact: `0` means every discovered non-structural member was safely processed;
`2` means reconciliation conflicts; `3` means known raw-preserved/unsupported members remain;
`4` means at least one matched member failed; and `5` means unknown/unclassified members remain.
When multiple conditions exist, failure takes precedence, followed by unknown, unsupported, and
conflict.

The PR5–PR11 stack implements the complete 68-product adapter contract for the retained Takeout
generation. Its whole-export routing audit accounts for all 25,427 data members (146.25 GB) with
zero unknown, ambiguous, generic-fallback, unsupported, or raw-preserved members. Products absent
from the retained export have synthetic contract fixtures and remain explicitly marked
`not_present_in_export`; binary content stays in its immutable source and is represented by
content-addressed external assets when processed.

### Inspect and import an official Apple privacy export

Keep the untouched Apple ZIPs together in their own acquisition folder, separate from iPhone or
iMessage exports:

```powershell
personal-data-lab inspect-apple data\raw\apple\2026-09-16 --format table
personal-data-lab import-apple-all data\raw\apple\2026-09-16
```

Apple commonly nests ZIPs inside the downloaded ZIPs. The inventory recursively accounts for
containers, guide PDFs, structured data, notes, contacts, calendars, and external media without
extracting over the source. It reports physical outer-archive bytes separately from logical leaf
bytes. Default JSON and progress output redact source and member paths because Notes filenames may contain
private content; `inspect-apple --include-paths` is an explicit diagnostic opt-in.

CSV rows are streamed in bounded chunks, Contacts and Calendars receive native VCF/ICS semantics,
Notes retain text and attachment relationships, and binary media remains source-backed. See
[`APPLE_INGESTION_SPEC.md`](APPLE_INGESTION_SPEC.md) and
[`APPLE_PRODUCT_COVERAGE.md`](APPLE_PRODUCT_COVERAGE.md). Parser v3 corrects review findings and marks generic structured coverage as partial (exit 3).
A fresh full-corpus acceptance run remains required. Historical parser v2 results are recorded in
[`PR14_APPLE_PRODUCTION_ACCEPTANCE_REPORT.md`](PR14_APPLE_PRODUCTION_ACCEPTANCE_REPORT.md).

### Inspect and import an independently acquired iMessages export

Keep device/backup exports outside the official Apple privacy-export folder. The retained source
uses iMazing CSV and contains timezone-naive timestamps, so import requires an explicit IANA
timezone:

```powershell
personal-data-lab inspect-imessages data\raw\imessages --format table
personal-data-lab import-imessages-all data\raw\imessages `
  --timezone America/Indianapolis `
  --device-label retained-iphone-export
```

Default inventory and progress output redact source paths. Use
`inspect-imessages --include-paths` only for local diagnostics. Missing attachment binaries are
retained as reference-only assets rather than silently discarded or falsely marked available.
See [`IMESSAGES_INGESTION_SPEC.md`](IMESSAGES_INGESTION_SPEC.md) and
[`IMESSAGES_COVERAGE.md`](IMESSAGES_COVERAGE.md). The retained corpus passed controlled
stop/resume and exact unchanged replay; see
[`PR15_IMESSAGES_PRODUCTION_ACCEPTANCE_REPORT.md`](PR15_IMESSAGES_PRODUCTION_ACCEPTANCE_REPORT.md).

## Permanent cumulative and lossless contract

PersonalDataLab is cumulative and lossless. Raw exports are immutable evidence. Every distinct
normalized payload ever observed is retained exactly once, while repeated identical payloads
reuse the existing version. Normal AI and application queries use the canonical tables, which
contain the best-known authoritative representation of each logical record.

The layers have separate jobs:

```text
raw source files
    → distinct normalized versions
        → one canonical representation per logical identity
            → narrow run/file/version observations
```

When ordering evidence cannot safely select between different payloads, both versions and the
conflicted observation are committed while canonical content remains unchanged. The CLI reports
the conflict and exits non-zero. Uncertainty is never resolved by throwing unique information
away or by treating local import order as provider chronology.

### Canonical identity and history

A logical provider record is canonical by `(source, source_record_namespace,
source_record_id)`. Conversations retain their provider conversation ID under the same
namespaced rule. Namespaces prevent IDs reused by different provider products or record types
from colliding.

Each canonical row stores a deterministic content fingerprint, first/last observation pointers,
the authority watermark selecting its current representation, and a content revision counter.
`conversation_versions` and `event_versions` store one full payload for every distinct normalized
representation. The corresponding observation points to that exact version. An unchanged newer
observation may advance the authority watermark, but it never increments `canonical_revision` or
changes `canonical_updated_at`.

Provider update times are authority evidence, not content. A timestamp-only observation reuses
the existing content version. Fingerprints produced by different algorithm versions are never
compared; the importer requires an explicit database migration before accepting a new fingerprint
version.

The database keeps both compatibility joins and run-scoped observations:

```text
source_files ── source_file_conversations ── conversations
source_files ── source_file_events ───────── events
import_runs ─── event_observations ───────── event_versions ── events
```

Observation rows store small keys and version pointers, not another copy of canonical text or
JSON. Only an explicit provider deletion signal creates a `provider_deleted` version and may
change canonical lifecycle state. Historical active versions remain queryable, and a newer
authoritative reappearance may reactivate the canonical row.

### Absence is never deletion: 100 → 75

If a 2025 spreadsheet contains 100 stable logical records and a 2027 export contains only 75,
the canonical corpus still contains all 100. The 2025 observation has 100 memberships; the 2027
observation has 75. The missing 25 retain their payload, lifecycle, revision, and 2025
`last_observed_run_id`. No tombstone or synthetic deletion is created. For the repeated 75,
`last_observed_run_id` advances to the 2027 run without creating duplicate versions.

These concepts are intentionally different:

- `canonical_run_id`: run that selected or authoritatively confirmed the current representation
- `last_observed_run_id`: latest ingestion run in which the record appeared, even if stale or
  conflicted
- snapshot membership: records linked by observations to one particular run/file
- historical existence: any retained logical record/version ever validly observed
- provider deletion: an explicit retained lifecycle fact, never inferred from absence

### Query layers for future AI and MCP use

Ordinary questions query `people`, `identities`, `conversations`, `events`, `records`, `assets`,
and `attachments`.
Historical-change questions query the corresponding version tables. Provenance or forensic
questions additionally query observations, import runs, source files, and source-file locations.
Agents should not compare raw exports or scan every provenance layer to answer normal questions;
canonical data is the default fast query surface.

For production OpenAI imports, `source_record_id` and `source_node_id` contain the
conversation-scoped mapping-node identity, while `parent_source_node_id` contains the matching
conversation-scoped parent identity. The provider message ID remains separately preserved in
metadata. This prevents provider message IDs reused across conversations from collapsing events.

## 9. Run tests

Unit tests do not require a database:

```powershell
pytest
ruff check .
```

Database integration tests require an explicitly disposable database whose name ends in
`_test`. These commands delete and recreate the Compose volume, so do not use them after
loading real data:

```powershell
$env:POSTGRES_DB = "personaldata_test"
$env:POSTGRES_USER = "personaldata_admin"
$env:POSTGRES_PASSWORD = "local-test-only"
$env:DATABASE_URL = "postgresql+psycopg://personaldata_admin:local-test-only@127.0.0.1:5432/personaldata_test"
$env:TEST_DATABASE_URL = $env:DATABASE_URL

docker compose down -v
docker compose up -d --wait
pytest
docker compose down -v
```

CI performs the same PostgreSQL 18/pgvector integration run with synthetic data and a
non-sensitive CI-only password.

## Data guarantees in this slice

- Raw exports are never modified by the importer.
- Every logical conversation and event exists once in the canonical corpus.
- Join tables link each canonical record to every export that contained it.
- OpenAI mapping nodes are conversation-scoped; provider message IDs and parent-node evidence
  remain separately preserved.
- Anthropic message UUIDs and parent-message UUIDs remain available for graph traversal.
- Anthropic provider content blocks, attachment metadata, and file references remain
  traceable without copying binary attachments into PostgreSQL.
- Messages without textual content are still retained as events, so reconciliation
  does not silently discard them.
- Re-importing an identical source is idempotent, and overlapping exports add only new
  canonical records while retaining complete provenance.
- Exact completed imports are short-circuited, and unchanged records in distinct overlapping
  exports do not rewrite canonical payloads.
- Older exports cannot regress newer canonical content; ambiguous changes are preserved as
  conflicted versions while canonical content remains unchanged.
- Records missing from later exports remain part of the cumulative historical corpus.
- Explicit provider deletion and later reactivation preserve every historical version.
- Raw-file identity is `(source, sha256)`: a second path for the same bytes adds location
  provenance, while different bytes at a reused path create a new retained source-file row.
- Attachments remain filesystem-backed; PostgreSQL stores metadata and paths only.
- The database is exposed only on the local machine.

## Provenance queries

Count unique OpenAI messages:

```sql
SELECT COUNT(*)
FROM events
WHERE source = 'openai'
  AND event_type = 'conversation_message';
```

Find every export containing a message:

```sql
SELECT sf.*
FROM source_files sf
JOIN source_file_events sfe ON sfe.source_file_id = sf.id
JOIN events e ON e.id = sfe.event_id
WHERE e.source = 'openai'
  AND e.metadata -> 'openai' ->> 'provider_message_id' = :message_id;
```

Reconstruct OpenAI parent relationships:

```sql
SELECT child.id AS child_id, parent.id AS parent_id
FROM events child
JOIN events parent
  ON parent.conversation_id = child.conversation_id
 AND parent.source_node_id = child.parent_source_node_id
WHERE child.source = 'openai';
```

## Branch and pull-request workflow

Development belongs on a feature branch. Do not commit implementation work directly
to `main`:

```powershell
git switch -c feature/anthropic-importer
# make and test changes
git push -u origin feature/anthropic-importer
gh pr create --base main --title "Add the Anthropic data importer"
```

Review the pull request before merging.

## Roadmap

Google ingestion is implemented through PR11 and production-accepted in PR13; see
[`PR13_GOOGLE_PRODUCTION_ACCEPTANCE_REPORT.md`](PR13_GOOGLE_PRODUCTION_ACCEPTANCE_REPORT.md).
Official Apple privacy-export ingestion is implemented with recursive accounting and remains a
separate provenance family from independently acquired iMessages; see
[`PR14_APPLE_PRODUCTION_ACCEPTANCE_REPORT.md`](PR14_APPLE_PRODUCTION_ACCEPTANCE_REPORT.md).
Parser-v3 real-corpus persistence, interruption/resume, and exact replay are accepted in
[`PR16_APPLE_V3_PRODUCTION_ACCEPTANCE_REPORT.md`](PR16_APPLE_V3_PRODUCTION_ACCEPTANCE_REPORT.md);
generic structured product coverage remains explicitly partial.
Independent iMazing Messages CSV ingestion preserves device-export provenance, message
lifecycle, reply/reaction semantics, and attachment availability without mixing it into the
official Apple source family; see
[`PR15_IMESSAGES_PRODUCTION_ACCEPTANCE_REPORT.md`](PR15_IMESSAGES_PRODUCTION_ACCEPTANCE_REPORT.md).
Official OpenAI ingestion is production-accepted against the retained manifest-based, sharded
export; see [`PR17_OPENAI_PRODUCTION_ACCEPTANCE_REPORT.md`](PR17_OPENAI_PRODUCTION_ACCEPTANCE_REPORT.md).
Full-text and semantic search,
identity resolution, derived claims, MCP, and agents follow the source-ingestion phases. See
[`ROADMAP.md`](ROADMAP.md) for the ordered program and completion gates.

