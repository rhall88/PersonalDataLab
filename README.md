# PersonalDataLab

A local, provenance-first foundation for importing personal data into PostgreSQL.

This repository contains the completed foundation and its first three source importers:

- Docker Compose
- PostgreSQL 18 plus pgvector
- canonical records (`people`, `identities`, `conversations`, `events`, and `attachments`)
- source-file provenance (`source_files`, `source_file_conversations`, and `source_file_events`)
- lossless distinct-version history plus narrow import-run/observation provenance
- cumulative reconciliation for inserts, genuine updates, unchanged overlap, stale data,
  conflicts, explicit deletion, and reactivation
- OpenAI data-export importer
- Anthropic data-export importer
- Google Takeout My Activity importer
- reconciliation tests
- Windows/PowerShell setup instructions

MCP, embeddings, other source importers, full-text search, analytics, DuckDB/Parquet, identity
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

```powershell
personal-data-lab import-openai data\raw\openai\2026-09-08\openai-export.zip
```

The command accepts an original ZIP, an extracted export directory, or a standalone
`conversations.json`. It runs in one database transaction, performs idempotent
canonical upserts, records source-file membership, and reconciles parsed counts against
membership rows for that particular export. A count mismatch raises an error and rolls
the entire transaction back.

Successful output includes per-entity reconciliation and version-deduplication metrics:

```json
{
  "source_file_id": 1,
  "source_conversations": 100,
  "database_conversations": 100,
  "source_events": 2500,
  "database_events": 2500,
  "inserted_events": 25,
  "updated_events": 4,
  "unchanged_events": 2471,
  "stale_events": 0,
  "conflicted_events": 0,
  "new_event_versions": 29,
  "reused_event_versions": 2471,
  "reconciled": true
}
```

`database_conversations` and `database_events` are the numbers of conversation and
event membership rows persisted for this `source_file_id`; they are not corpus-wide
canonical totals.

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

This first Google slice imports the common My Activity records used by Search, YouTube,
Maps, Chrome, Play, Shopping, and Ads. JSON is preferred because its timestamps are
machine-readable; the HTML fallback preserves activity-card text and links, but leaves
localized dates unset when they cannot be interpreted safely.

The importer scans ZIP central directories and opens only supported activity members. It
does not extract or read photo, video, Drive, or other binary payloads. Each supported member
is hashed while it is parsed and becomes a separate `source_files` row whose location names
both the immutable ZIP and member. Split archives are processed in filename order, and each
member commits independently so a later malformed member can be corrected and the command
rerun without duplicating completed work.

Google does not provide a durable record ID for My Activity rows. The adapter therefore uses
a deterministic digest of the complete provider record, with stable occurrence suffixes for
identical duplicates. The same unchanged activity receives the same logical identity in a
later export. All provider fields remain available under `metadata.google`; the normalized
`time` is stored as `occurred_at` instead of being duplicated in fingerprinted metadata.

Gmail MBOX content, Calendar, Contacts, Drive document extraction, Google Photos sidecars and
binaries, and raw Location History tracks are deliberately deferred to product-specific
parsers. Unsupported products are left untouched in the retained Takeout rather than being
silently represented as successfully imported.

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

Ordinary questions query `people`, `identities`, `conversations`, `events`, and `attachments`.
Historical-change questions query the corresponding version tables. Provenance or forensic
questions additionally query observations, import runs, source files, and source-file locations.
Agents should not compare raw exports or scan every provenance layer to answer normal questions;
canonical data is the default fast query surface.

For OpenAI messages, `source_record_id` contains `message.id`, `source_node_id` contains
the mapping-node ID, and `parent_source_node_id` contains the structural parent-node ID.
This keeps the message and graph identifier namespaces distinct. If a message lacks an
ID, the importer explicitly falls back to its mapping-node ID and marks that fallback in
event metadata.

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
- OpenAI message IDs, mapping-node IDs, and parent-node IDs remain distinct.
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
  AND e.source_record_id = :message_id;
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

## Deferred roadmap

Product-specific Google parsers, embeddings, semantic search, MCP servers, agent workflows,
generic filesystem deduplication, and binary delta chains remain future work.
