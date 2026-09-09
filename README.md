# PersonalDataLab

A local, provenance-first foundation for importing personal data into PostgreSQL.

This repository contains **only the first vertical slice**:

- Docker Compose
- PostgreSQL 18 plus pgvector
- canonical records (`people`, `identities`, `conversations`, `events`, and `attachments`)
- source-file provenance (`source_files`, `source_file_conversations`, and `source_file_events`)
- one importer: OpenAI data exports
- reconciliation tests
- Windows/PowerShell setup instructions

MCP, embeddings, other importers, full-text search, analytics, DuckDB/Parquet, identity
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

### Reset the pre-merge development database

This PR changes the initial schema. Because the first slice has not been merged yet,
reset the temporary development database instead of introducing a migration framework:

```powershell
docker compose down -v
docker compose up -d --wait
```

**Warning:** `docker compose down -v` permanently deletes the local PostgreSQL volume
and every database record in it. It does not touch immutable exports under `data/raw/`.
Only run it while this database is disposable or after making an appropriate backup.

## 4. Install the importer

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## 5. Store the untouched export

Keep the original OpenAI ZIP unchanged. For example:

```text
data/raw/openai/2026-09-08/openai-export.zip
```

Everything below `data/raw/` is ignored by Git. Do not edit, replace, or unpack over
the original ZIP. The importer reads the archive directly and records its SHA-256,
path, size, parser version, and ingestion time.

## 6. Import and reconcile

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

## Canonical records and export provenance

A logical OpenAI conversation is canonical by `(source, source_conversation_id)`. A
logical message is canonical by `(source, source_record_id)`. Importing overlapping
exports therefore does not duplicate conversations or messages. The join tables retain
every observation:

```text
source_files ── source_file_conversations ── conversations
source_files ── source_file_events ───────── events
```

For OpenAI messages, `source_record_id` contains `message.id`, `source_node_id` contains
the mapping-node ID, and `parent_source_node_id` contains the structural parent-node ID.
This keeps the message and graph identifier namespaces distinct. If a message lacks an
ID, the importer explicitly falls back to its mapping-node ID and marks that fallback in
event metadata.

## 7. Run tests

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
- Messages without textual content are still retained as events, so reconciliation
  does not silently discard them.
- Re-importing an identical source is idempotent, and overlapping exports add only new
  canonical records while retaining complete provenance.
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
git switch -c feature/first-vertical-slice
# make and test changes
git push -u origin feature/first-vertical-slice
gh pr create --base main --title "Build the first vertical slice"
```

Review the pull request before merging.

## Deferred roadmap

After this PR is reviewed and merged, the intended order is: Claude importer,
iMessage, Meta, Google, Apple, corpus-wide reconciliation, identity resolution,
ordinary search and analytics, a read-only AI database account, generic PostgreSQL
MCP, custom personal-data MCP, embeddings, Parquet/DuckDB, then evidence-backed
derived analysis. None of those features belong in this slice.
