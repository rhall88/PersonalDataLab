-- Phase 7 (PHASE7_SEMANTIC_ANNOTATION_IMPLEMENTATION_PLAN.md): semantic annotation over Phase 6's
-- semantic.text_unit population. signal.annotation is named directly in
-- DERIVED_MEANING_ARCHITECTURE.md's own schema table (unit_id, kind, value, score, versions).
-- Like every other derived-meaning table, it carries no foreign key FROM any canonical table --
-- only a reference TO semantic.text_unit -- so this whole table can be dropped and rebuilt from
-- semantic.text_unit at any time (invariant 20), and a SEMANTIC-class row is never mistaken for
-- ground truth by anything reading it (invariant 7).

CREATE TABLE signal.annotation (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    text_unit_id BIGINT NOT NULL REFERENCES semantic.text_unit(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('topic', 'event_mention', 'intent')),
    value TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL CHECK (score >= 0 AND score <= 1),
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (text_unit_id, kind)
);

CREATE INDEX annotation_text_unit_idx ON signal.annotation (text_unit_id);
CREATE INDEX annotation_kind_value_idx ON signal.annotation (kind, value);
