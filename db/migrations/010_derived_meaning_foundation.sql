-- Phase 0 of the derived meaning architecture (DERIVED_MEANING_ARCHITECTURE.md): the machinery
-- that lets derived data exist without ever touching canonical evidence. No canonical table is
-- altered by this migration -- everything lives in four new schemas.
--
-- The rest of this schema uses TEXT + CHECK for enum-like columns, and this migration follows
-- that convention with one deliberate exception: policy.egress_level is a native Postgres ENUM,
-- because Postgres orders enum values by declaration order, which lets GREATEST() compute the
-- egress lattice's max-restriction directly (architecture inv. 24, 29). That ordering is a real
-- dependency here, not a style choice, so a native type earns its keep.

CREATE SCHEMA derive;
CREATE SCHEMA declare;
CREATE SCHEMA policy;
CREATE SCHEMA measure;

CREATE TYPE policy.egress_level AS ENUM ('external_model_allowed', 'redactable', 'local_only');

-- An evidence epoch closes after a completed import command and records the highest import_runs
-- id it covers. Import runs are per-member (Google Takeout alone produces tens of thousands), so
-- they are too fine-grained for a version vector; an epoch is the unit "as of import N" actually
-- means. As-of reconstruction itself needs no new columns -- it replays *_versions/*_observations
-- with the existing authority rules (reconciliation.py) up to an epoch's max_import_run_id.
CREATE TABLE derive.evidence_epoch (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    max_import_run_id BIGINT NOT NULL REFERENCES import_runs(id),
    closed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note TEXT
);

CREATE INDEX evidence_epoch_max_import_run_id_idx
    ON derive.evidence_epoch (max_import_run_id);

-- A deriver is a named, registered computation. final_inference records its epistemic class
-- (architecture sec. 1) -- COMPUTED and STATISTICAL are the only classes Phase 0 derivers use;
-- the rest exist for later phases, but the CHECK is written for all seven now so no later
-- migration has to widen it.
CREATE TABLE derive.deriver (
    name TEXT PRIMARY KEY,
    output_relation TEXT NOT NULL,
    partition_class TEXT NOT NULL CHECK (partition_class IN ('LOCAL', 'HALO', 'GLOBAL')),
    lookback_halo INTERVAL NOT NULL DEFAULT '0',
    lookahead_halo INTERVAL NOT NULL DEFAULT '0',
    final_inference TEXT NOT NULL CHECK (final_inference IN (
        'OBSERVED', 'COMPUTED', 'STATISTICAL', 'SEMANTIC', 'DECLARED', 'HYPOTHESIS', 'NARRATIVE'
    )),
    description TEXT NOT NULL,
    CHECK (partition_class = 'HALO' OR (lookback_halo = '0' AND lookahead_halo = '0'))
);

-- version_hash = sha256(canonical_json({name, code_version, params, model_ref})). A deriver can
-- have many versions; at most one is 'active' at a time, and older versions stay queryable until
-- explicitly retired so a past answer can still be replayed against the version that produced it
-- (architecture inv. 31).
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

CREATE INDEX deriver_version_deriver_name_idx ON derive.deriver_version (deriver_name);

CREATE UNIQUE INDEX deriver_version_one_active_idx
    ON derive.deriver_version (deriver_name) WHERE status = 'active';

-- Dependency edges between derivers, enforced acyclic at registration time in Python (a single
-- CHECK cannot forbid a multi-row cycle). This is the acyclic requirement architecture inv. 35
-- states for claims generally; Phase 0 has no claims yet, but the dependency graph plumbing is
-- shared, so the constraint against self-reference lives here from the start.
CREATE TABLE derive.deriver_dependency (
    upstream TEXT NOT NULL REFERENCES derive.deriver(name),
    downstream TEXT NOT NULL REFERENCES derive.deriver(name),
    PRIMARY KEY (upstream, downstream),
    CHECK (upstream <> downstream)
);

-- One row per (deriver_version, partition_key). This is the dirty/clean state the worker claims
-- and the scheduler marks -- the only mutable table in the derive schema.
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

CREATE INDEX partition_state_dirty_idx
    ON derive.partition_state (deriver_version_id) WHERE state = 'dirty';

-- Append-only audit trail of every attempt, kept independent of partition_state so a failed or
-- skipped attempt is never silently lost when the partition later succeeds.
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

CREATE INDEX derivation_run_deriver_version_id_idx
    ON derive.derivation_run (deriver_version_id, partition_key);

-- The change feed's bookmark: the last import_runs.id whose observations have been read and
-- turned into dirty partitions. A single row per named cursor (Phase 0 needs exactly one).
CREATE TABLE derive.change_cursor (
    name TEXT PRIMARY KEY,
    last_import_run_id BIGINT NOT NULL DEFAULT 0
);

INSERT INTO derive.change_cursor (name, last_import_run_id) VALUES ('default', 0);

-- Declarations (architecture inv. 23): append-only, user-asserted facts. Never overwritten --
-- a correction supersedes the old row via `supersedes` rather than updating it in place, so the
-- full history of what you declared and when stays inspectable. Phase 0 creates this table
-- because policy.egress_override references it; nothing writes declarations of kind other than
-- 'egress_override' until later phases, but the CHECK lists the kinds the architecture already
-- commits to so it doesn't need widening for each one.
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

CREATE INDEX declaration_kind_idx ON declare.declaration (kind);

-- Egress policy (architecture sec. 7, inv. 24 and 29): what may leave the machine, kept as its
-- own versioned table. Named egress_policy_version, not policy_version, specifically so it is
-- never confused with the unrelated import_runs.policy_version (parser/import policy) -- the two
-- have nothing to do with each other and previously shared a name by accident.
CREATE TABLE policy.egress_policy_version (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    definition JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Per-source defaults for the version they were declared under. sensitivity is a free-form label
-- set (source-level granularity for now; finer-grained sensitivity arrives with metric
-- definitions in a later phase). egress is the lattice value a deriver reading this source
-- inherits unless an override says otherwise.
CREATE TABLE policy.source_policy (
    source TEXT NOT NULL,
    egress_policy_version_id BIGINT NOT NULL REFERENCES policy.egress_policy_version(id),
    sensitivity TEXT[] NOT NULL DEFAULT '{}',
    egress policy.egress_level NOT NULL DEFAULT 'local_only',
    PRIMARY KEY (source, egress_policy_version_id)
);

-- Loosening egress below what the lattice would otherwise compute always requires an override
-- tied to a declaration -- never automatic (architecture inv. 24: "loosening only via a recorded
-- egress_override tied to a declaration"). to_level < from_level enforces that this table can
-- only ever loosen, never tighten (tightening needs no override -- it's just the default).
CREATE TABLE policy.egress_override (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    object_ref TEXT NOT NULL,
    from_level policy.egress_level NOT NULL,
    to_level policy.egress_level NOT NULL,
    declaration_id BIGINT NOT NULL REFERENCES declare.declaration(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (to_level < from_level)
);

CREATE INDEX egress_override_object_ref_idx ON policy.egress_override (object_ref);

-- Seed: egress policy version 1, all six sources local_only (inv. 29 -- nothing leaves the
-- machine until explicitly authorized). Sensitivity defaults: intimate for the two sources that
-- carry personal/relationship messaging, private for the rest. Both are source-level starting
-- points, not a final answer, and either can be loosened later only via a declared override.
-- Single-user scope for Phase 0: nothing here is scoped by user, because there is only one.
INSERT INTO policy.egress_policy_version (definition)
VALUES ('{"note": "Phase 0 defaults: all sources local_only, seeded 2026-09-21"}'::jsonb);

INSERT INTO policy.source_policy (source, egress_policy_version_id, sensitivity, egress)
SELECT source, 1, sensitivity, 'local_only'
FROM (VALUES
    ('anthropic', ARRAY['private']::TEXT[]),
    ('openai', ARRAY['private']::TEXT[]),
    ('imessages', ARRAY['intimate']::TEXT[]),
    ('apple', ARRAY['private']::TEXT[]),
    ('google', ARRAY['private']::TEXT[]),
    ('facebook', ARRAY['intimate']::TEXT[])
) AS defaults(source, sensitivity);

-- The two Phase 0 derivers' output tables. Created here so `DROP SCHEMA measure CASCADE`
-- followed by a rebuild is the literal, testable form of "the derived layer is disposable"
-- (architecture inv. 20).
CREATE TABLE measure.source_activity_daily (
    source TEXT NOT NULL,
    day DATE NOT NULL,
    object_kind TEXT NOT NULL,
    active_count BIGINT NOT NULL,
    tombstoned_count BIGINT NOT NULL,
    versions BIGINT NOT NULL,
    sensitivity TEXT[] NOT NULL,
    egress policy.egress_level NOT NULL,
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    PRIMARY KEY (source, day, object_kind)
);

CREATE TABLE measure.source_activity_7d (
    source TEXT NOT NULL,
    day DATE NOT NULL,
    object_kind TEXT NOT NULL,
    active_count BIGINT NOT NULL,
    tombstoned_count BIGINT NOT NULL,
    versions BIGINT NOT NULL,
    sensitivity TEXT[] NOT NULL,
    egress policy.egress_level NOT NULL,
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    PRIMARY KEY (source, day, object_kind)
);

-- day is UTC for both tables, explicitly provisional until Phase 1 derives each source's local
-- time zone. Recorded here, not just in application code, so a query against these tables can't
-- silently assume local-time semantics that don't exist yet.
COMMENT ON COLUMN measure.source_activity_daily.day IS
    'UTC calendar day. Provisional -- see Phase 1 (time normalization) for local time.';
COMMENT ON COLUMN measure.source_activity_7d.day IS
    'UTC calendar day, trailing 7-day window ending on this day. Same provisional note as '
    'measure.source_activity_daily.day.';

-- derive.deriver and derive.deriver_dependency are deliberately NOT seeded here. A deriver's
-- definition (its halo, params, description) is code, registered by the @deriver decorator in
-- registry.py the first time it runs; a schema migration hard-coding that would need a new
-- migration every time a deriver's parameters change, defeating the point of having a registry.
