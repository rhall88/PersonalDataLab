# Phase 0 implementation plan: derivation and epistemic foundation

Status: proposed, 2026-09-21. Implements Phase 0 of
[`DERIVED_MEANING_ARCHITECTURE.md`](DERIVED_MEANING_ARCHITECTURE.md) against the repository as it
stands at schema version 9 (`c397dd2`).

## Summary

Phase 0 adds the machinery that lets derived data exist without touching canonical evidence: a
`derive` schema with versioned derivers, partition state and a `SKIP LOCKED` worker; `declare` and
`policy` schemas; and one real, deliberately boring pair of derivers that prove the machinery end
to end. It needs **zero importer changes**, matching how search (v8) and identity (v9) landed.

The biggest finding from reading the repo: the evidence layer already *is* the canonical revision
log the architecture asks for. `*_versions` plus `*_observations` keyed by `import_runs`, with
authority-based (not ingestion-order) reconciliation, already make as-of reconstruction a
deterministic query. The architecture's proposed `arrived_in_import` / `superseded_in_import`
columns are unnecessary and should be dropped from the doc (see "Corrections to the architecture").

## What the repository already provides

| Architecture concept | Already in the repo | Gap Phase 0 closes |
| --- | --- | --- |
| L0 raw, content-addressed | `source_files` (sha256, per source), retained raw exports | None |
| L1 canonical evidence | `conversations`, `events`, `records`, `assets`, `attachments`, `relationships` | None |
| Canonical revision log (inv. 22, sec. 18) | `*_versions` (immutable, fingerprinted) + `*_observations` (per run, with `outcome`) | A named, ordered **evidence epoch** so version vectors can say "as of epoch N" |
| Order-independent reconciliation | `reconciliation.py` authority rules | None. This is what makes as-of replay deterministic |
| Provider deletion | `lifecycle_state = 'provider_deleted'`, `tombstone_run_id` | Maps directly to `value_basis = provider_deleted` later |
| Provenance family | `source` column (anthropic, openai, imessages, apple, google, facebook); iMessages already kept separate from Apple | `evidence_root` edges come later (sec. 18) |
| Import policy versions | `import_runs.parser_version`, `fingerprint_version`, `policy_version` | Name collision: see decisions below |
| Human identity assertions | `identity_links` (append-only, revertible) | Becomes a DECLARED input in Phase 2 |
| Schema migrations | `db/migrations/NNN_*.sql`, mirrored into `db/init/002_schema.sql`, `LATEST_SCHEMA_VERSION` | Migration 010 |
| Test harness | pytest, `TEST_DATABASE_URL` integration tests, CI with pgvector/pg18 | New test modules |

Scale today: about 1.2M canonical rows (643k records, 525k events, 2.1k conversations, 22.5k assets),
about 25k import runs. Every Phase 0 query is comfortable at this size without DuckDB.

## Corrections to the architecture doc

1. **As-of reconstruction uses observations, not new columns.** Canonical state as of epoch N is
   the result of applying the existing authority rules to observations from runs in epochs up to N.
   No columns are added to 1.2M canonical rows, and no in-place supersession updates are needed.
   Section 3's `arrived_in_import` / `superseded_in_import` rows and section 18's "revision log"
   paragraph should be rewritten to point at `*_versions` / `*_observations`.
2. **"Import N" is an evidence epoch, not an import run.** Import runs are per member (Google
   alone has 23k), so they are too fine-grained for a version vector. An epoch closes after each
   completed import command and records the highest terminal run id it covers.
3. **`identity_links` and `actor_person_id` are a documented deviation.** `actor_person_id` is
   derived meaning written onto canonical rows (unfingerprinted, so evidence integrity holds, but it
   breaks invariant 1's spirit and has no resolution version). Phase 0 leaves it alone. Phase 2
   imports `identity_links` into the assertion log as DECLARED assertions and turns
   `actor_person_id` into a projection of the active resolution version.

## Scope

**In:** `derive`, `declare` and `policy` schemas; epistemic and egress types; deriver registry,
version hashing, partition classes and halos; change feed from observations; dirty propagation;
`SKIP LOCKED` worker; rebuild; shadow/promote; egress lattice and propagation; two real derivers;
CLI; tests; application to the canonical database.

**Out:** time normalization and local time zones, observation contracts and coverage (Phase 1);
identity resolution versions (Phase 2); metric catalog, life ledger, DuckDB (Phase 3); anything
semantic or LLM-based.

## Migration 010 (sketch)

Real Postgres schemas, not table prefixes. The architecture requires that any derived layer can be
destroyed and rebuilt (invariant 20); `DROP SCHEMA measure CASCADE` followed by `derive rebuild` is
the literal test of that, and schemas also give clean grant boundaries later.

```sql
CREATE SCHEMA derive;
CREATE SCHEMA declare;
CREATE SCHEMA policy;
CREATE SCHEMA measure;

-- Epistemic classes (sec. 1). Stored as a signature, never ranked (sec. 18).
CREATE TYPE derive.epistemic_class AS ENUM (
    'OBSERVED', 'COMPUTED', 'STATISTICAL', 'SEMANTIC', 'DECLARED', 'HYPOTHESIS', 'NARRATIVE'
);

-- Egress lattice. Enum declaration order IS the lattice order, so GREATEST() computes
-- MAX_RESTRICTION directly (inv. 24).
CREATE TYPE policy.egress_level AS ENUM ('external_model_allowed', 'redactable', 'local_only');

CREATE TABLE derive.evidence_epoch (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    max_import_run_id BIGINT NOT NULL REFERENCES import_runs(id),
    closed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note TEXT
);

CREATE TABLE derive.deriver (
    name TEXT PRIMARY KEY,
    output_relation TEXT NOT NULL,
    partition_class TEXT NOT NULL CHECK (partition_class IN ('LOCAL', 'HALO', 'GLOBAL')),
    lookback_halo INTERVAL NOT NULL DEFAULT '0',
    lookahead_halo INTERVAL NOT NULL DEFAULT '0',
    final_inference derive.epistemic_class NOT NULL,
    description TEXT NOT NULL,
    CHECK (partition_class = 'HALO' OR (lookback_halo = '0' AND lookahead_halo = '0'))
);

CREATE TABLE derive.deriver_version (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deriver_name TEXT NOT NULL REFERENCES derive.deriver(name),
    code_version TEXT NOT NULL,
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    model_ref JSONB,
    version_hash BYTEA NOT NULL UNIQUE CHECK (octet_length(version_hash) = 32),
    status TEXT NOT NULL CHECK (status IN ('shadow', 'active', 'retired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX deriver_version_one_active
    ON derive.deriver_version (deriver_name) WHERE status = 'active';

CREATE TABLE derive.deriver_dependency (
    upstream TEXT NOT NULL REFERENCES derive.deriver(name),
    downstream TEXT NOT NULL REFERENCES derive.deriver(name),
    PRIMARY KEY (upstream, downstream),
    CHECK (upstream <> downstream)
);

CREATE TABLE derive.partition_state (
    deriver_version_id BIGINT NOT NULL REFERENCES derive.deriver_version(id),
    partition_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('dirty', 'running', 'clean', 'failed')),
    input_fingerprint BYTEA,
    dirty_since TIMESTAMPTZ,
    computed_at TIMESTAMPTZ,
    computed_epoch_id BIGINT REFERENCES derive.evidence_epoch(id),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    PRIMARY KEY (deriver_version_id, partition_key)
);
CREATE INDEX partition_state_dirty ON derive.partition_state (deriver_version_id)
    WHERE state = 'dirty';

CREATE TABLE derive.derivation_run (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deriver_version_id BIGINT NOT NULL REFERENCES derive.deriver_version(id),
    partition_key TEXT NOT NULL,
    epoch_id BIGINT REFERENCES derive.evidence_epoch(id),
    input_fingerprint BYTEA,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'skipped_unchanged', 'failed')),
    rows_written BIGINT,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    stats JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE derive.change_cursor (
    name TEXT PRIMARY KEY,
    last_import_run_id BIGINT NOT NULL DEFAULT 0
);

-- Declarations (inv. 23): append-only, versioned by supersession, never overwritten.
CREATE TABLE declare.declaration (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN (
        'episode', 'identity_same_as', 'identity_not_same_as', 'metric_definition',
        'label', 'egress_override', 'exclude', 'purge'
    )),
    payload JSONB NOT NULL,
    valid_during TSTZRANGE,
    supersedes BIGINT REFERENCES declare.declaration(id),
    declared_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Egress policy (sec. 7, inv. 24 and 29).
CREATE TABLE policy.policy_version (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    definition JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE policy.source_policy (
    source TEXT NOT NULL,
    policy_version_id BIGINT NOT NULL REFERENCES policy.policy_version(id),
    sensitivity TEXT[] NOT NULL DEFAULT '{}',
    egress policy.egress_level NOT NULL DEFAULT 'local_only',
    PRIMARY KEY (source, policy_version_id)
);
CREATE TABLE policy.egress_override (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    object_ref TEXT NOT NULL,
    from_level policy.egress_level NOT NULL,
    to_level policy.egress_level NOT NULL,
    declaration_id BIGINT NOT NULL REFERENCES declare.declaration(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (to_level < from_level)
);
```

Seed data: policy version 1 with all six sources `local_only` (inv. 29: nothing leaves the machine
until you authorize it). Sensitivity defaults: `intimate` for imessages and facebook (source-level for now; finer
granularity comes with metric definitions), `private` elsewhere, adjustable later by declaration.

The `measure` output tables for the two Phase 0 derivers are created by the migration too, so
`DROP SCHEMA measure CASCADE` plus rebuild can be tested.

Existing conventions to keep: mirror the migration into `db/init/002_schema.sql`, bump
`LATEST_SCHEMA_VERSION` to 10, extend `tests/test_migrations.py`. Because importers call
`_require_latest_schema`, the canonical database must be migrated before the next import, exactly
as with versions 8 and 9.

## Python runtime: `personal_data_lab/derive/`

| Module | Responsibility |
| --- | --- |
| `registry.py` | `@deriver(name=..., partition_class=..., halo=..., final_inference=..., inputs=[...])` decorator; syncs `derive.deriver` and `deriver_dependency` rows; rejects dependency cycles at registration (inv. 35) |
| `versioning.py` | `version_hash = sha256(canonical_json({name, code_version, params, model_ref}))`; creates shadow versions; `promote()` flips active in one transaction |
| `partitions.py` | Partition key helpers (`source:YYYY-MM`), halo expansion over month keys, GLOBAL keys |
| `changefeed.py` | Reads `*_observations` for runs after the cursor; ignores `unchanged` and `stale` outcomes; maps changed canonical objects to partition keys; advances the cursor and closes an evidence epoch |
| `scheduler.py` | Marks partitions dirty and propagates downstream through the DAG, honoring halos; GLOBAL partitions are marked dirty but only run on an explicit `derive run --global` or after the batch settles |
| `worker.py` | Claims dirty partitions with `FOR UPDATE SKIP LOCKED`, computes the input fingerprint, skips unchanged inputs (`skipped_unchanged`), otherwise deletes and rewrites the partition's output rows in one transaction |
| `policy.py` | Egress lattice (`max_restriction`), sensitivity union, override validation; every deriver write goes through it |
| `derivers/activity.py` | The two Phase 0 derivers below |

### Change feed without importer changes

Importers already record an outcome per object per run. The change feed reads only observations
with outcome `inserted`, `updated`, `conflicted` or `tombstoned` for runs past the cursor, joins to
the canonical row for its date, and emits `(source, month)` partition keys. An exact replay produces
only `unchanged` outcomes, so it dirties nothing. That is the Phase 0 exit test, and it falls out of
the existing design for free.

## The two Phase 0 derivers

They are deliberately simple so any failure is a machinery bug, not a statistics bug. They are also
real: coverage in Phase 1 needs exactly this data.

| Deriver | Class | Output | Partition | Notes |
| --- | --- | --- | --- | --- |
| `source_activity_daily` | LOCAL | `measure.source_activity_daily(source, day, object_kind, active_count, tombstoned_count, versions, sensitivity, egress)` | `source:YYYY-MM` | COMPUTED; UTC days for now, flagged as provisional until Phase 1 time normalization |
| `source_activity_7d` | HALO, lookback 6 days | `measure.source_activity_7d(...)` trailing 7-day sums | `source:YYYY-MM` | Exercises halo propagation across month boundaries |

Input fingerprint for a partition: a hash over `(id, canonical_revision, lifecycle_state)` of the
canonical rows in that source and month, ordered by id. Cheap at current scale; revisit if a
single partition exceeds a few million rows. Rows without a usable date go to a `source:undated`
partition rather than being dropped.

## CLI

Following the existing `personal-data-lab <verb>` style and the guarded-engine read/admin path:

```
personal-data-lab derive sync          # read change feed, close epoch, mark dirty
personal-data-lab derive run           # process dirty LOCAL/HALO partitions
personal-data-lab derive run --global  # also process GLOBAL partitions
personal-data-lab derive status        # per deriver: active version, dirty/failed counts, last epoch
personal-data-lab derive rebuild [--deriver NAME]   # drop outputs, mark all dirty, run
personal-data-lab derive promote NAME VERSION_HASH  # shadow -> active
personal-data-lab declare add KIND --payload JSON
personal-data-lab policy show
```

## Delivery as three PRs

| PR | Contents | Gate |
| --- | --- | --- |
| A. Schema v10 | Migration 010, snapshot mirror, seeds, `LATEST_SCHEMA_VERSION`, migration tests | Fresh install and upgrade from v9 both pass; no canonical table altered |
| B. Runtime | `derive/` modules, CLI, unit and integration tests | Tests below pass in CI |
| C. Derivers and rollout | Two derivers, policy propagation, apply to the canonical database, record in `PROJECT_STATE.md` | Exit tests below pass on the real 1.2M-row database |

## Exit tests

1. **Replay dirties nothing.** Re-import an unchanged fixture export: `derive sync` marks zero
   partitions dirty.
2. **Late history dirties only its months.** Importing a fixture with 2014 data dirties only the
   affected `source:2014-MM` partitions plus their 7-day halo neighbors.
3. **Incremental equals full rebuild.** After a sequence of imports, a full `derive rebuild`
   produces byte-identical output checksums to the incremental path.
4. **Derived layer is disposable (inv. 20).** `DROP SCHEMA measure CASCADE`, recreate, `derive
   rebuild`: identical checksums. No canonical row changes (compare canonical table checksums before
   and after).
5. **Unchanged input skips.** A second `derive run` with no changes records only
   `skipped_unchanged`.
6. **Versioning.** Changing a deriver's params yields a new hash; the shadow version writes
   separate rows; `promote` flips active atomically; the old version's rows stay queryable until
   retired.
7. **Concurrency.** Two workers on the same dirty set never process the same partition.
8. **Policy propagation.** Output egress equals the lattice max of inputs; loosening without an
   `egress_override` backed by a declaration is rejected; an override records its declaration.
9. **Cycle rejection.** Registering a dependency cycle fails.
10. **Real database.** Migrate the canonical database, `derive sync && derive run` across all six
    sources; record timing, row counts and the zero-drift replay check in `PROJECT_STATE.md`.

## Decisions for Ry

1. **Postgres schemas** (`derive`, `declare`, `policy`, `measure`) instead of table prefixes in
   `public`. Recommended, for invariant 20 and future grants.
2. **Naming collision:** `import_runs.policy_version` means *import* policy. The new egress policy
   uses `policy.policy_version`; the plan never touches the existing column. Acceptable, or rename
   the new one `egress_policy_version`?
3. **UTC days** for the Phase 0 derivers, explicitly provisional until Phase 1 derives local time.
4. **Default sensitivities**: `intimate` for iMessages and Facebook (whole source for now),
   `private` for the rest, all `local_only`.
