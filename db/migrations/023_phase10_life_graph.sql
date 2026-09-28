-- Phase 10 (PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md, v3): the life graph. One consolidated
-- generative pass (generative.extraction, SEMANTIC) over semantic.text_unit, plus pure-logic
-- explode/resolve/candidate derivers over its output -- entities, relations, events, decisions,
-- open loops, all as candidate/confirm pairs (never a direct auto-applied write), and three new
-- know.claim templates over the confirmed results.

CREATE SCHEMA IF NOT EXISTS generative;

CREATE TABLE generative.extraction (
    text_unit_id BIGINT PRIMARY KEY REFERENCES semantic.text_unit(id) ON DELETE CASCADE,
    interaction_primary TEXT CHECK (interaction_primary IN (
        'support', 'conflict', 'planning', 'affection', 'logistics', 'financial',
        'creative_collaboration', 'advice', 'argument', 'reconciliation'
    )),
    interaction_secondary TEXT CHECK (interaction_secondary IN (
        'support', 'conflict', 'planning', 'affection', 'logistics', 'financial',
        'creative_collaboration', 'advice', 'argument', 'reconciliation'
    )),
    salience TEXT NOT NULL CHECK (salience IN ('low', 'medium', 'high')),
    entity_mentions JSONB NOT NULL DEFAULT '[]'::jsonb,
    relation_candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
    event_type TEXT CHECK (event_type IN (
        'purchase', 'employment_change', 'relationship_change', 'relocation',
        'health_event', 'legal_event', 'milestone', 'other'
    )),
    event_summary TEXT,
    event_actor_slot INTEGER,
    event_object_slot INTEGER,
    event_location_slot INTEGER,
    event_time_expression TEXT,
    decision_state TEXT NOT NULL DEFAULT 'none' CHECK (decision_state IN (
        'none', 'considering', 'decided', 'implemented', 'abandoned', 'reversed'
    )),
    decision_summary TEXT,
    open_loop_state TEXT NOT NULL DEFAULT 'none' CHECK (open_loop_state IN (
        'none', 'opened', 'continued', 'resolved', 'abandoned'
    )),
    open_loop_summary TEXT,
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (interaction_secondary IS NULL OR interaction_secondary != interaction_primary)
);

CREATE SCHEMA IF NOT EXISTS entity;

-- Never written by a deriver -- created only via `entity-create`, exactly how `people` rows are
-- only ever created via `identity-create-person`.
CREATE TABLE entity.entity (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('organization', 'place', 'project', 'named_concept')),
    canonical_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- One row per mention OCCURRENCE -- explode-only, no resolution here.
CREATE TABLE entity.mention (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    text_unit_id BIGINT NOT NULL REFERENCES semantic.text_unit(id) ON DELETE CASCADE,
    slot_index INTEGER NOT NULL,
    surface_text TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('person', 'organization', 'place', 'project', 'named_concept')),
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (text_unit_id, slot_index)
);

-- Proposed resolutions -- candidate/confirmed/rejected, never a direct active write.
-- Exactly one of the two candidate-target columns is set; a real CHECK, not app-level trust.
CREATE TABLE entity.link_candidate (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mention_id BIGINT NOT NULL REFERENCES entity.mention(id) ON DELETE CASCADE,
    candidate_person_id BIGINT REFERENCES people(id) ON DELETE CASCADE,
    candidate_entity_id BIGINT REFERENCES entity.entity(id) ON DELETE CASCADE,
    resolution_tier TEXT NOT NULL CHECK (resolution_tier IN ('exact_match', 'fuzzy_match', 'manual')),
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'confirmed', 'rejected')),
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (num_nonnulls(candidate_person_id, candidate_entity_id) = 1)
);

-- The two real, active resolution tables -- written only by entity-confirm/entity-merge (CLI),
-- never by a deriver. Twins by design: same reversible shape, different target.
CREATE TABLE entity.entity_link (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mention_id BIGINT NOT NULL REFERENCES entity.mention(id) ON DELETE CASCADE,
    entity_id BIGINT NOT NULL REFERENCES entity.entity(id) ON DELETE CASCADE,
    lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK (lifecycle_state IN ('active', 'reverted')),
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reverted_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX entity_link_active_idx ON entity.entity_link (mention_id) WHERE lifecycle_state = 'active';

CREATE TABLE entity.person_link (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mention_id BIGINT NOT NULL REFERENCES entity.mention(id) ON DELETE CASCADE,
    person_id BIGINT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK (lifecycle_state IN ('active', 'reverted')),
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reverted_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX person_link_active_idx ON entity.person_link (mention_id) WHERE lifecycle_state = 'active';

-- Unifies both resolution kinds so relation edges never have to care which table a given mention
-- happened to resolve into.
CREATE VIEW entity.resolved_mention AS
    SELECT mention_id, 'person'::text AS resolved_kind, person_id AS resolved_id
    FROM entity.person_link WHERE lifecycle_state = 'active'
    UNION ALL
    SELECT mention_id, 'entity'::text AS resolved_kind, entity_id AS resolved_id
    FROM entity.entity_link WHERE lifecycle_state = 'active';

-- Exploded relation candidates -- still slot-indexed, not yet resolved.
CREATE TABLE entity.relation_candidate (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    text_unit_id BIGINT NOT NULL REFERENCES semantic.text_unit(id) ON DELETE CASCADE,
    subject_slot INTEGER NOT NULL,
    relation_type TEXT NOT NULL CHECK (relation_type IN (
        'created', 'uses', 'works_at', 'member_of', 'owns', 'located_at',
        'participated_in', 'about', 'supports', 'opposes', 'part_of', 'influenced'
    )),
    object_slot INTEGER NOT NULL,
    evidence_mode TEXT NOT NULL CHECK (evidence_mode IN ('explicit', 'implied')),
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (subject_slot != object_slot)
);

CREATE VIEW entity.relation_edge AS
    SELECT
        rc.id AS relation_candidate_id,
        subj.resolved_kind AS subject_kind, subj.resolved_id AS subject_id,
        rc.relation_type,
        obj.resolved_kind AS object_kind, obj.resolved_id AS object_id,
        rc.evidence_mode, rc.text_unit_id
    FROM entity.relation_candidate rc
    JOIN entity.mention subj_m ON subj_m.text_unit_id = rc.text_unit_id AND subj_m.slot_index = rc.subject_slot
    JOIN entity.resolved_mention subj ON subj.mention_id = subj_m.id
    JOIN entity.mention obj_m ON obj_m.text_unit_id = rc.text_unit_id AND obj_m.slot_index = rc.object_slot
    JOIN entity.resolved_mention obj ON obj.mention_id = obj_m.id;

CREATE SCHEMA IF NOT EXISTS event;

-- Deterministic-code output, kept OUT of generative.extraction.
CREATE TABLE event.time_resolution (
    text_unit_id BIGINT PRIMARY KEY REFERENCES semantic.text_unit(id) ON DELETE CASCADE,
    raw_expression TEXT NOT NULL,
    resolved_range DATERANGE,
    resolution_rule TEXT NOT NULL,
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Proposed only -- event-confirm/event-reject (CLI) does the real multi-table write.
CREATE TABLE event.cluster_candidate (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type TEXT NOT NULL,
    proposed_label TEXT NOT NULL,
    occurred_range DATERANGE,
    member_text_unit_ids JSONB NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'confirmed', 'rejected')),
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE event.canonical_event (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type TEXT NOT NULL,
    label TEXT NOT NULL,
    occurred_range DATERANGE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE event.event_link (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_event_id BIGINT NOT NULL REFERENCES event.canonical_event(id) ON DELETE CASCADE,
    text_unit_id BIGINT NOT NULL REFERENCES semantic.text_unit(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE SCHEMA IF NOT EXISTS intent;

-- Proposed only -- intent-confirm/intent-reject (CLI) does the real write.
CREATE TABLE intent.cluster_candidate (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('decision', 'open_loop')),
    proposed_label TEXT NOT NULL,
    member_text_unit_ids JSONB NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'confirmed', 'rejected')),
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE intent.thread (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('decision', 'open_loop')),
    label TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE intent.thread_link (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread_id BIGINT NOT NULL REFERENCES intent.thread(id) ON DELETE CASCADE,
    text_unit_id BIGINT NOT NULL REFERENCES semantic.text_unit(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE know.claim_contradiction (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_id_a BIGINT NOT NULL REFERENCES know.claim(id) ON DELETE CASCADE,
    claim_id_b BIGINT NOT NULL REFERENCES know.claim(id) ON DELETE CASCADE,
    note TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'confirmed', 'rejected')),
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (claim_id_a < claim_id_b)
);

ALTER TABLE know.claim DROP CONSTRAINT claim_template_check;
ALTER TABLE know.claim ADD CONSTRAINT claim_template_check
    CHECK (template IN (
        'communication_change', 'episode_evidence', 'relationship_state_shift',
        'event_claim', 'decision_claim', 'open_loop_claim'
    ));
