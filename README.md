# PersonalDataLab

A local, provenance-first foundation for importing personal data into PostgreSQL.

This repository contains **only the first vertical slice**:

- Docker Compose
- PostgreSQL 18 plus pgvector
- the initial schema (`source_files`, `people`, `identities`, `conversations`, `events`, and `attachments`)
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
docker compose up -d
docker compose ps
docker compose exec db psql -U personaldata_admin -d personaldata -c "SELECT version();"
docker compose exec db psql -U personaldata_admin -d personaldata -c "SELECT extversion FROM pg_extension WHERE extname = 'vector';"
```

The database is bound to `127.0.0.1`, persists in the `postgres_data` Docker volume,
and initializes pgvector plus the schema on the first start.

To apply the schema to an already-created volume after changing initialization SQL:

```powershell
Get-Content db\init\001_extensions.sql,db\init\002_schema.sql |
  docker compose exec -T db psql -U personaldata_admin -d personaldata
```

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
upserts, and compares source conversation/message counts with database counts. A
count mismatch raises an error and rolls the transaction back.

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

## 7. Run tests

```powershell
pytest
ruff check .
```

## Data guarantees in this slice

- Raw exports are never modified by the importer.
- Every event points to the source file record and original source record ID.
- Messages without textual content are still retained as events, so reconciliation
  does not silently discard them.
- Re-importing the same byte-identical source is idempotent.
- Attachments remain filesystem-backed; PostgreSQL stores metadata and paths only.
- The database is exposed only on the local machine.

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
