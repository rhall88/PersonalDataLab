-- Source policy for the eighth source, `ios_messages` (the iPhone's own Messages database from the
-- device backup, holding the iMessages/SMS/RCS the iMazing CSV source lacks). Same policy as
-- `imessages`: `intimate` sensitivity, `local_only` egress (architecture inv. 29). Without a row,
-- the activity derivers refuse the source outright.
INSERT INTO policy.source_policy (source, egress_policy_version_id, sensitivity, egress)
VALUES ('ios_messages', 1, ARRAY['intimate']::TEXT[], 'local_only')
ON CONFLICT DO NOTHING;
