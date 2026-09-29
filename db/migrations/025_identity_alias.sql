-- Identity bootstrap: the other names a person goes by ("Sarah", "Sarah Hallgarth", "Sarah
-- Stonewall Jackson"). entity_resolve_mentions previously matched text mentions only against
-- people.display_name, so one exact spelling per person. Aliases are human-confirmed (usually
-- via a contact card, see identity_contacts.py) and revert rather than delete, like
-- identity_links.

CREATE TABLE identity.alias (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id BIGINT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    alias TEXT NOT NULL CHECK (btrim(alias) <> ''),
    normalized_alias TEXT NOT NULL CHECK (normalized_alias <> ''),
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    lifecycle_state TEXT NOT NULL DEFAULT 'active'
        CHECK (lifecycle_state IN ('active', 'reverted')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reverted_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX alias_person_normalized_active_idx
    ON identity.alias (person_id, normalized_alias) WHERE lifecycle_state = 'active';
CREATE INDEX alias_normalized_active_idx
    ON identity.alias (normalized_alias) WHERE lifecycle_state = 'active';
