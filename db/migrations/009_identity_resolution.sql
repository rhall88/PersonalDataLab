-- events already carries an unused actor_person_id column from the original schema; records
-- never got the equivalent. Add it so both tables can be annotated once an identity is resolved.
ALTER TABLE records ADD COLUMN actor_person_id BIGINT REFERENCES people(id) ON DELETE SET NULL;

-- A small, purpose-built table for reversible, user-asserted identity links. Deliberately not
-- the dormant identities table (predates the versioning/lifecycle conventions used everywhere
-- else in this schema and has no way to represent a reverted link) and not the relationships
-- table (fingerprinted and authority-reconciled for provider-evidenced structural edges between
-- canonical objects -- a different trust model than a human asserting "these are the same
-- person").
CREATE TABLE identity_links (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    person_id BIGINT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    identity_value TEXT NOT NULL,
    note TEXT,
    lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK (
        lifecycle_state IN ('active', 'reverted')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reverted_at TIMESTAMPTZ
);

-- One active person per (source, identity_value) at a time. A split flips lifecycle_state to
-- 'reverted' rather than deleting the row, and a later merge always inserts a new row rather
-- than reusing one -- the full merge/split history is permanent and inspectable.
CREATE UNIQUE INDEX identity_links_active_idx
    ON identity_links (source, identity_value)
    WHERE lifecycle_state = 'active';

CREATE INDEX identity_links_person_id_idx ON identity_links (person_id);
