-- Source policy for the seventh source, `ios_backup` (iPhone device backup: Voice Memos,
-- voicemail, every app's recordings). Same starting point as the other personal-messaging sources:
-- `intimate` sensitivity, `local_only` egress (architecture inv. 29 -- nothing leaves the machine
-- until explicitly authorized; loosening only ever via a declared override). Without a row, the
-- activity derivers refuse the source outright.
INSERT INTO policy.source_policy (source, egress_policy_version_id, sensitivity, egress)
VALUES ('ios_backup', 1, ARRAY['intimate']::TEXT[], 'local_only')
ON CONFLICT DO NOTHING;
