# PersonalDataLab

A local, provenance-first foundation for importing personal data into PostgreSQL.

This repository contains the completed foundation and its first two source importers:

- Docker Compose
- PostgreSQL 18 plus pgvector
- canonical records (`people`, `identities`, `conversations`, `events`, and `attachments`)
- source-file provenance (`source_files`, `source_file_conversations`, and `source_file_events`)
- import-run and per-observation provenance with deterministic canonical fingerprints
- cumulative reconciliation for inserts, genuine updates, unchanged overlap, and stale data
- OpenAI data-export importer
- Anthropic data-export importer
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

The command is idempotent. It backfills provider namespaces and canonical fingerprints and
converts existing source-file memberships into compact legacy observations. Those migrated
observations are marked as membership-only provenance because the old schema did not retain the
content fingerprint seen in each historical export.

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
└── openai/
    └── 2026-09-08/
        └── openai-export.zip
```

Everything below `data/raw/` is ignored by Git. Do not edit, replace, or unpack over
the original files. The importers read ZIP archives directly and record each source
archive's SHA-256, path, size, parser version, export time when supplied, and ingestion
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

Successful output resembles:

```json
{
  "source_file_id": 1,
  "source_conversations": 100,
  "database_conversations": 100,
  "source_events": 2500,
  "database_events": 2500,
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

## Canonical records and export provenance

A logical provider record is canonical by `(source, source_record_namespace,
source_record_id)`. Conversations retain their provider conversation ID under the same
namespaced rule. Namespaces prevent IDs reused by different provider products or record types
from colliding.

Each canonical row stores a deterministic content fingerprint, the authority timestamp that
selected its current representation, and a revision counter. Imports classify observations as
new, updated, unchanged, or stale. Unchanged overlap does not update the canonical row, and an
older export imported later cannot overwrite a newer authoritative representation. Differing
content with equal or incomparable authority fails closed instead of using invocation order.

The database keeps both compatibility joins and run-scoped observations:

```text
source_files ── source_file_conversations ── conversations
source_files ── source_file_events ───────── events
import_runs ─── event_observations ───────── events
```

Observation rows store keys and fingerprints, not another copy of canonical text or JSON. A later
export that omits an older record does not delete or deactivate it: absence is not a provider
deletion signal.

For OpenAI messages, `source_record_id` contains `message.id`, `source_node_id` contains
the mapping-node ID, and `parent_source_node_id` contains the structural parent-node ID.
This keeps the message and graph identifier namespaces distinct. If a message lacks an
ID, the importer explicitly falls back to its mapping-node ID and marks that fallback in
event metadata.

## 8. Run tests

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
- Older exports cannot regress newer canonical content; ambiguous equal-authority changes fail.
- Records missing from later exports remain part of the cumulative historical corpus.
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

After the Anthropic importer is reviewed and merged, the intended order is: iMessage,
Meta, Google, Apple, corpus-wide reconciliation, identity resolution,
ordinary search and analytics, a read-only AI database account, generic PostgreSQL
MCP, custom personal-data MCP, embeddings, Parquet/DuckDB, then evidence-backed
derived analysis. None of those features belong in the Anthropic-importer slice.
