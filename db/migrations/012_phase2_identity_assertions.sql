-- Phase 2 of the derived meaning architecture (DERIVED_MEANING_ARCHITECTURE.md, section 5):
-- identity resolution. No canonical table (events/records/etc.) is altered by this migration.
-- Migration 009 already added `people` and `identity_links` -- a working, reversible, purely
-- manual instance of the architecture's tier 3 (You/DECLARED). This migration adds the
-- append-only log underneath it (PHASE2_IMPLEMENTATION_PLAN.md's "the log is the source of
-- truth; clusters are a derived, versioned view") and the scaffolding tiers 1 and 2 need, without
-- redesigning identity_links itself, which already has the right reversibility semantics.

CREATE SCHEMA IF NOT EXISTS identity;

-- One resolution_version per identity-changing operation (a CLI merge/split call, or one
-- rules-tier run), not per identity.assertion row -- a single operation that writes several
-- assertion rows (e.g. a rules-tier pass linking multiple groups) can share one version number
-- instead of each row claiming its own, without a later redesign of this column.
CREATE SEQUENCE identity.resolution_version_seq;

-- The append-only log identity_links was missing: every merge, split, and negative declaration,
-- with which tier made it and why. identity_links remains the fast current-state read path
-- (unchanged unique-active-per-(source, identity_value) index from migration 009); this table is
-- what identity_links' lifecycle_state transitions are now derived from, and what the rules tier
-- and identity-not-same-as actually consult before acting.
CREATE TABLE identity.assertion (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    resolution_version BIGINT NOT NULL,
    tier TEXT NOT NULL CHECK (tier IN ('computed', 'declared')),
    action TEXT NOT NULL CHECK (action IN ('link', 'unlink', 'not_same_as')),
    source TEXT NOT NULL,
    identity_value TEXT NOT NULL,
    person_id BIGINT REFERENCES people(id) ON DELETE SET NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX identity_assertion_source_value_idx
    ON identity.assertion (source, identity_value);

-- A permanent negative declaration: this (source, identity_value) is never the same person as
-- person_id (or, with person_id NULL, is never auto-linked to anything by the rules tier at
-- all). Required before any auto-linking exists -- PHASE2_IMPLEMENTATION_PLAN.md's whole
-- rules-tier design depends on this being checkable before a link is written, not bolted on
-- after the first wrong auto-link is made.
CREATE TABLE identity.not_same_as (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    identity_value TEXT NOT NULL,
    person_id BIGINT REFERENCES people(id) ON DELETE CASCADE,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source, identity_value, person_id)
);

-- Which tier made the currently-active link, and which resolution_version it came from --
-- so a reader of identity_links alone (without joining identity.assertion) can already tell a
-- rules-tier auto-link from a human's declared one. Existing rows (migration 009's own two real
-- links, both made by hand before this migration existed) default to 'declared', which is the
-- accurate classification for every row written before the rules tier existed.
--
-- IF NOT EXISTS here (unlike the plain CREATEs above): identity_links is the one object this
-- migration touches that lives outside the fresh `identity` schema, so it isn't reset by
-- anything that drops and recreates that schema. tests/test_migrations.py's pr2_engine fixture
-- does exactly that between tests -- it drops+recreates `identity` for isolation, then replays
-- this whole migration file against the shared database to restore it, and a second replay must
-- not fail just because the column already exists on the one pre-existing table involved.
ALTER TABLE identity_links ADD COLUMN IF NOT EXISTS resolution_version BIGINT;
ALTER TABLE identity_links ADD COLUMN IF NOT EXISTS tier TEXT NOT NULL DEFAULT 'declared'
    CHECK (tier IN ('computed', 'declared'));
