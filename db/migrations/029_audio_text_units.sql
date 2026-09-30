-- Audio into the semantic layer. Until now text units came only from message events and document
-- units only from records, so Whisper output in media.transcript (564 non-empty transcripts as of
-- 2026-09-30) was stored but unsearchable, and the iPhone's own transcripts reached documents only.
--
-- semantic.text_unit (the message layer that extraction, topic labels and clustering read) gains
-- provenance for units that come from a recording rather than from events: the record carrying a
-- provider transcript (Voice Memos, voicemail) or the asset whose Whisper transcript it is, plus
-- the chunk index when one long recording becomes several units. event_ids stays NOT NULL ('{}'
-- for these units), so every existing reader keeps working.
ALTER TABLE semantic.text_unit
    ADD COLUMN record_id BIGINT REFERENCES records(id),
    ADD COLUMN asset_id BIGINT REFERENCES assets(id),
    ADD COLUMN chunk_index INTEGER;

-- semantic.text_unit_document can now come from a transcribed asset as well as a record, and a
-- long document is split into chunks instead of being cut off at 8,000 characters.
ALTER TABLE semantic.text_unit_document
    ALTER COLUMN record_id DROP NOT NULL,
    ADD COLUMN asset_id BIGINT REFERENCES assets(id),
    ADD COLUMN chunk_index INTEGER NOT NULL DEFAULT 0,
    DROP CONSTRAINT text_unit_document_record_id_key,
    ADD CONSTRAINT text_unit_document_origin_check CHECK (num_nonnulls(record_id, asset_id) = 1);

CREATE UNIQUE INDEX text_unit_document_record_chunk_idx
    ON semantic.text_unit_document (record_id, chunk_index) WHERE record_id IS NOT NULL;
CREATE UNIQUE INDEX text_unit_document_asset_chunk_idx
    ON semantic.text_unit_document (asset_id, chunk_index) WHERE asset_id IS NOT NULL;
