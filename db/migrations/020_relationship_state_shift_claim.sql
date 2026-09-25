-- ROADMAP.md item 3's "further claim-template growth": adds relationship_state_shift to
-- know.claim's template CHECK constraint -- one claim per consecutive pair of
-- signal.interaction_state segments for the same (source, correspondent), wrapping Phase 4's
-- already-computed PELT segmentation the same way episode_evidence wraps episode detection.

ALTER TABLE know.claim DROP CONSTRAINT claim_template_check;
ALTER TABLE know.claim ADD CONSTRAINT claim_template_check
    CHECK (template IN ('communication_change', 'episode_evidence', 'relationship_state_shift'));
