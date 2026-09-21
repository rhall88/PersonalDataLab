# Unified canonical full-text query specification

## Scope

`personal-data-lab search` provides provenance-aware full-text search across the four canonical
entity kinds — `conversations`, `events`, `records`, and `assets` — spanning all six accepted
source families (Anthropic, OpenAI, iMessages, Apple, Google, Facebook) in one ranked result list.
It is the first piece of the "Analytics Layer" described in
[`COCKPIT_VISION.md`](COCKPIT_VISION.md); nothing beyond this read-only query surface is built yet.

## Storage: a column, not a second source of truth

Each of the four canonical tables carries its own `search_vector tsvector` column and `GIN` index,
maintained by a `BEFORE INSERT OR UPDATE` trigger (`db/migrations/008_full_text_search.sql`). There
is no separate search-index table: search is just another indexed column on tables that already
exist, so it inherits their existing provenance, versioning, and tombstone semantics for free, and
requires zero changes to any importer, adapter, or orchestrator. Every existing and future
insert/update across all six sources becomes searchable automatically, proven directly: updating a
record's text through the normal import path re-indexes it with no call into any search code
(`tests/test_search.py::test_updating_a_record_through_the_normal_import_path_reindexes_it_automatically`).

A `GENERATED ALWAYS AS ... STORED` column was considered and rejected: PostgreSQL disallows
`to_tsvector('english', ...)` in a generated column because the `english` text-search configuration
is STABLE, not IMMUTABLE. A trigger keeps real stemming and stop-word handling instead of falling
back to the dictionary-free `simple` configuration.

## Weighting

Each table weights its own fields (`setweight`, so a title/text match ranks above an incidental
metadata match):

| Table | A (highest) | B | C | D (lowest) |
|---|---|---|---|---|
| `conversations` | `title` | — | — | `metadata::text` (capped) |
| `events` | `text` | `actor_identity` | `event_type` | `metadata::text` (capped) |
| `records` | `title` | `text` | `record_type` | `metadata::text` (capped) |
| `assets` | `filename` (normalized, see below) | — | `mime_type` | `metadata::text` (capped) |

`metadata::text` is capped at 10,000 characters (`left(metadata::text, 10000)`) before indexing.
Some stored payloads (for example CSV-chunk records) can be large, and metadata is the lowest-weight,
completeness-only signal, not the primary search target.

### Filename normalization

Asset filenames are rarely sentences: they're opaque hex/numeric IDs (Facebook's content-addressed
`{sha256}{extension}` filenames, Apple's UUIDs) or human names joined with underscores/hyphens (for
example `sourdough_starter.jpg`). PostgreSQL's default text-search parser treats a string like that
as one opaque token, so a plain `to_tsvector` would never match `sourdough` alone — confirmed
directly against real behavior before shipping this. `filename_search_text()` (a small `IMMUTABLE`
SQL function defined by the migration) replaces `_`, `-`, and `.` with spaces before indexing, and
the same function is used when building the highlighted snippet so highlighting and matching stay
consistent.

## Ranking and query syntax

Queries are parsed with `websearch_to_tsquery('english', ...)` — the same friendly syntax a search
engine accepts (quoted phrases, `OR`, `-exclusion`) rather than `to_tsquery`'s strict operator
syntax. Results are ranked with `ts_rank_cd(search_vector, tsquery, 32)` (cover-density ranking,
length-normalized via the `32` flag) so that a short conversation title and a long record's body
text are compared fairly rather than favoring whichever field happens to be longer.

## Query shape

One SQL statement per call: a `WITH q AS (SELECT websearch_to_tsquery(...))` CTE computes the parsed
query once, each requested kind contributes a `SELECT ... WHERE search_vector @@ q.tsq` leg with the
same column shape, the legs are combined with `UNION ALL`, and the outer query ranks and limits
across all of them together. Kinds not requested (`--kind`) are not queried at all, not filtered out
after the fact.

## Provenance in every hit

Every `SearchHit` carries: `kind`, `id`, `source`, `namespace` (the entity's
`source_record_namespace`/`asset_namespace`), `identifier` (its `source_*_id`), `title`, a
`ts_headline`-highlighted `snippet`, `occurred_at`, `lifecycle_state`, `canonical_run_id`, and
`rank`. This is enough to trace any hit back to the import run and canonical row that produced it,
matching the evidence-first principle in [`COCKPIT_VISION.md`](COCKPIT_VISION.md) section 6 — a
future UI can resolve `(source, namespace, identifier)` to the exact canonical row and its full
observation/version history without this search layer needing to expose that history itself.

## Lifecycle and filtering

- `lifecycle_state = 'active'` rows are returned by default; `--include-deleted` includes
  provider-tombstoned rows too. Nothing is ever excluded from the index itself — filtering happens
  at query time, so a later `--include-deleted` search never requires re-indexing.
- `--source` (repeatable) restricts to one or more of `apple`, `google`, `facebook`, `openai`,
  `anthropic`, `imessages`.
- `--kind` (repeatable) restricts to one or more of `conversation`, `event`, `record`, `asset`.
- `--limit` (default 25) bounds the ranked result count.

## CLI

```powershell
personal-data-lab search "sourdough bread" --format table
personal-data-lab search "sourdough" --source apple --source facebook --kind record --limit 10
personal-data-lab search "marmalade" --include-deleted
```

Default output is JSON (`{"query", "kinds", "sources", "include_deleted", "limit", "hits": [...]}`).
`search` is a read-only command — it uses the same `_resolve_guarded_engine(..., canonical_import=False)`
path as `migrate`, not `doctor`'s unguarded diagnostic engine, since it is a normal read against the
configured target rather than a tool for inspecting arbitrary databases.

## Verification

- Unit/integration suite (`tests/test_search.py`, requires `TEST_DATABASE_URL`): matches across all
  four kinds in one call, a title match outranking a metadata-only match, tombstoned rows excluded
  by default and included on request, `--source`/`--kind` filtering, automatic re-indexing on update
  through the normal import path with no `search.py` involvement, and the CLI command end-to-end.
- Manually verified against the real canonical database (see [`PROJECT_STATE.md`](PROJECT_STATE.md)
  for the migration rollout evidence and example real-query results).
