ALTER TABLE conversations
    ALTER COLUMN source_record_namespace SET NOT NULL,
    ALTER COLUMN content_fingerprint SET NOT NULL,
    ALTER COLUMN fingerprint_version SET NOT NULL;

ALTER TABLE events
    ALTER COLUMN source_record_namespace SET NOT NULL,
    ALTER COLUMN content_fingerprint SET NOT NULL,
    ALTER COLUMN fingerprint_version SET NOT NULL;

ALTER TABLE attachments
    ALTER COLUMN source_record_namespace SET NOT NULL,
    ALTER COLUMN source_attachment_id SET NOT NULL,
    ALTER COLUMN content_fingerprint SET NOT NULL,
    ALTER COLUMN fingerprint_version SET NOT NULL;

ALTER TABLE conversations
    ADD CONSTRAINT conversations_canonical_identity_key
    UNIQUE (source, source_record_namespace, source_conversation_id);

ALTER TABLE events
    ADD CONSTRAINT events_canonical_identity_key
    UNIQUE (source, source_record_namespace, source_record_id);

ALTER TABLE attachments
    ADD CONSTRAINT attachments_canonical_identity_key
    UNIQUE (source, source_record_namespace, source_attachment_id);

ALTER TABLE conversations
    DROP CONSTRAINT conversations_source_source_conversation_id_key;

ALTER TABLE events
    DROP CONSTRAINT events_source_source_record_id_key;

ALTER TABLE conversations
    ADD CONSTRAINT conversations_content_fingerprint_length
    CHECK (octet_length(content_fingerprint) = 32);

ALTER TABLE events
    ADD CONSTRAINT events_content_fingerprint_length
    CHECK (octet_length(content_fingerprint) = 32);

ALTER TABLE attachments
    ADD CONSTRAINT attachments_content_fingerprint_length
    CHECK (octet_length(content_fingerprint) = 32);
