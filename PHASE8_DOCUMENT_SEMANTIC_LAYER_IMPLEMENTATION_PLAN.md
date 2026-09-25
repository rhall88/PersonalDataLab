# Phase 8 implementation plan: documents into the semantic layer

`PHASE6_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md` scoped `semantic_text_unit` to `events` only --
messages, mail, AI chat turns. It never considered `records`, the canonical table for whole
documents (Drive files, NotebookLM notebooks/sources, Recorder transcripts, notes). That gap
surfaced by accident on 2026-09-25 while investigating whether Whisper was re-transcribing audio
that already has a text source elsewhere in the corpus (it is, in part -- see grounding below).
This plan scopes bringing `records`' genuinely prose-like content into the same semantic layer
`semantic-search`/`semantic_embedding_bge_small`/`signal_annotation_topic` already read, without
touching `events` or duplicating Phase 6's own scope.

## Grounding (queried against the real database, 2026-09-25)

- **`records` already holds 97,402 rows** across the record_types that could plausibly carry
  embeddable prose (`document`, `notebook`, `transcript`, `conversation`, `note`), but the
  overwhelming majority is the same kind of event-log noise Phase 6 excluded from `events`:
  51,825 of them are `google_takeout/drive/document/csv` -- Google Drive/Activity CSV exports
  (e.g. "Activities - A list of Google services accessed by...", one row per app-access event),
  not documents a person wrote or read. Restricting to genuinely prose-like content gives a much
  smaller, real population:

  | source | namespace | rows | avg chars | what it actually is (sampled directly) |
  |---|---|---:|---:|---|
  | google | `notebooklm/document/html` | 290 | 58,600 | NotebookLM "Audio Overview" source documents and generated notes -- real prose |
  | google | `drive/document/html` | 47 | 25,978 | Saved/exported web pages and Drive HTML docs |
  | google | `drive/document/docx` | 20 | 45,654 | Real Word documents |
  | google | `drive/document/txt` | 8 | 302,518 | Real plain-text documents |
  | google | `notebooklm/document/md` | 5 | 8,937 | NotebookLM markdown notes |
  | google | `notebooklm/notebook/json` | 28 | 440 | Notebook titles/metadata (thin, but real) |
  | google | `recorder/document/txt` | 39 | 3,460 | **Real, verbatim, timestamped, speaker-labeled transcripts of Recorder-app voice memos** (confirmed by reading real rows: "Speaker 1 3:13:13 Always more...") |
  | google | `gemini/conversation/html`, `gemini_workspace/conversation/txt` | ~9 | varies | Real Gemini chat sessions (prompts + responses) |
  | apple | `notes/note/record` | 149 | 1,326 | Real Apple Notes content |

  **Directly actionable finding, not just background**: the `recorder/document/txt` rows are the
  text Google's own on-device Recorder transcription already produced for 39+ of the 45
  Recorder-sourced audio files currently being processed by `media_transcribe_whisper` (item 6,
  still running as of this plan) -- real, multi-speaker, timestamped transcripts, not mere
  summaries as first assumed when this was raised. That overlap is not undone by this plan (the
  running job is not stopped -- no partial commits, 18+ hours of already-necessary Apple/Facebook/
  other-Google work would be lost), but it is a real, avoidable-next-time inefficiency worth
  fixing before any future transcription rollout: check for an existing `records` transcript
  before spending CPU re-deriving one via Whisper.
- **Excluded, and why, not just omitted**: `drive/document/csv` (event-log noise, per above);
  `drive/document/xlsx` (14 rows, 604,961 avg chars -- spreadsheet cell-dump text extracted from
  binary format, not prose; embedding raw cell fragments would produce low-quality vectors
  matching nothing meaningfully, the same "don't invent value where none exists" discipline Phase
  6 applied to non-conversational `events`); `mail/thread/mbox`-namespaced `conversation` rows
  (verified directly: `text` is empty on every row, a structural thread pointer only -- the real
  Gmail content is already covered by Phase 6's `mail_message` event-sourced text units, so
  including these would double-count, not add coverage); every low-text-value namespace visible
  in the wider grounding query (`account`, `saved_item`, `device`, `calendar_event_definition`,
  `survey_response`, `subscription`, `photo_metadata`, `purchase`, `apple_music_top_content`,
  `facebook_notifications`, etc.) -- structured metadata, not documents.
- **99.97% of the in-scope population (97,372 of 97,402 rows) has neither `occurred_at` nor
  `created_at` set.** This is the exact undated-bucket shape `media_transcribe_whisper`'s own
  rollout found and fixed in `scheduler.py`'s `_root_keys_from_contracts` (2026-09-25, same day)
  -- that fix already seeds every declared source's undated bucket unconditionally, so this
  phase's own root deriver inherits correct backfill behavior for free, not a gap to rediscover.
- No existing table can hold this without a schema decision (see decision 2): `semantic.text_unit`
  ties every row to `event_ids BIGINT[]`, a foreign concept for a document that isn't built from
  canonical `events` rows at all.

## Decisions

1. **Scope: the eight namespaces in the grounding table above, one construction rule (one text
   unit per record), not a new grouping/chunking scheme.** Unlike Phase 6's messages (which
   needed session-grouping because a single message is rarely a coherent unit), a `records` row
   is already a coherent, bounded document -- no invented grouping rule needed. The existing
   8,000-char cap-with-`truncated`-flag (Phase 6 decision 2) applies unchanged, including to the
   two long-tail namespaces here (`drive/document/txt` at 302K avg chars, `notebooklm/
   document/html` at up to ~58K) -- most of those units will be marked `truncated = true`, an
   honest, already-established outcome, not a new failure mode to invent handling for.
2. **Schema: a new table, `semantic.text_unit_document`, not a reused/widened `semantic.
   text_unit`.** `semantic.text_unit.event_ids BIGINT[]` has no meaningful value for a
   record-sourced unit, and overloading one array column to sometimes mean "event ids" and
   sometimes "record ids" depending on an unrelated `source` value is exactly the kind of
   ambiguous shared-column design this codebase's own provenance-citation discipline (`claim_why`
   re-fetching every cited row by an unambiguous reference) argues against. `semantic.
   text_unit_document` mirrors `semantic.text_unit`'s shape exactly except `event_ids BIGINT[]` ->
   `record_id BIGINT NOT NULL REFERENCES records(id)` (singular -- one record is already one unit,
   no array needed). A **second, parallel embedding table**, `semantic.
   embedding_bge_small_en_v15_document`, keyed to `text_unit_document_id` -- not a shared
   embedding table keyed by a union type, for the same reason: `semantic_search`'s existing
   `<=>` query joins a fixed embedding table to a fixed text-unit table by a typed foreign key,
   and a nearest-neighbor query that has to `UNION` two differently-shaped sources for every
   search is a bigger, riskier change to an already-shipped, real-rollout-verified query than
   this phase needs to make. `semantic-search`'s CLI gains a `--include-documents` flag that runs
   both queries and merges results by distance, rather than the underlying tables merging.
3. **Model: the same pinned `BAAI/bge-small-en-v1.5` via `fastembed`, no new embedding space.**
   Nothing about record-sourced text changes the embedding model decision Phase 6 already made
   and verified against 70,145 real vectors; a second embedding space would only be justified by
   a reason to think prose from documents embeds meaningfully differently than prose from
   messages, and there isn't one.
4. **Runtime: two new derivers, mirroring Phase 6's own semantic_text_unit/semantic_embedding_
   bge_small split exactly.** `semantic_text_unit_document` (root, no inputs, partition_class
   HALO with the same generous 1-day halo Phase 6 used, reading `records` instead of `events`,
   filtered to the grounded namespace allowlist) and `semantic_embedding_document_bge_small`
   (LOCAL, `inputs=("semantic_text_unit_document",)`). Both ride the existing framework
   unchanged -- no new partition class, no new scheduler behavior, the same `backfill_new_
   derivers`/undated-bucket-seeding path every deriver since Phase 1 has used.
5. **`signal_annotation_topic` and `know_claim_evaluate` are explicitly out of scope for this
   plan.** Once `semantic.text_unit_document` exists, extending topic annotation or claim
   templates to read it too is a natural follow-up, but bundling it here would restate Phase 7's
   own "narrowest usable slice" discipline for no reason -- this plan's exit test is `semantic-
   search` finding real document content, not a new claim type.

## Schema (next migration, number TBD at implementation time)

```sql
CREATE TABLE semantic.text_unit_document (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    record_id BIGINT NOT NULL REFERENCES records(id),
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,
    text TEXT NOT NULL,
    truncated BOOLEAN NOT NULL DEFAULT FALSE,
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (record_id)
);

CREATE INDEX text_unit_document_source_started_idx ON semantic.text_unit_document (source, started_at);

CREATE TABLE semantic.embedding_bge_small_en_v15_document (
    text_unit_document_id BIGINT PRIMARY KEY
        REFERENCES semantic.text_unit_document(id) ON DELETE CASCADE,
    vector vector(384) NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX embedding_bge_small_en_v15_document_hnsw_idx
    ON semantic.embedding_bge_small_en_v15_document
    USING hnsw (vector vector_cosine_ops);
```

`started_at`/`ended_at` both derive from `COALESCE(occurred_at, created_at, updated_at)` -- the
same fallback chain, and same undated-bucket consequence (99.97% of rows), already established
and already handled correctly by the scheduler fix cited in grounding.

## Runtime

- `derive/derivers/semantic_documents.py` (new file, not folded into `semantic.py` -- keeps
  Phase 6's event-sourced code and this record-sourced code visibly separate, matching decision 2's
  reasoning that these are deliberately not the same concept wearing the same column names):
  - `_NAMESPACE_ALLOWLIST: frozenset[str]` -- the eight namespaces from the grounding table,
    a versioned constant exactly like Phase 6's `_SOURCE_EVENT_TYPES` and Phase 7's
    `_TOPIC_LABELS`; changing it is a fingerprint-visible, re-derivable decision, not a silent one.
  - `semantic_text_unit_document` (HALO root): per partition, selects `records` rows whose
    `source_record_namespace` is in the allowlist and whose fallback date falls in the partition
    window, builds one unit per record (truncated at 8,000 chars, same helper Phase 6 already
    has), `clear_partition` deletes this partition's own rows only (same isolation-test pattern
    `test_media.py`/`test_annotation.py` already established).
  - `semantic_embedding_document_bge_small` (LOCAL, `inputs=("semantic_text_unit_document",)`):
    identical shape to `semantic_embedding_bge_small`, reusing `semantic.embed_texts()` (already a
    public function in `semantic.py`) rather than re-loading a second copy of the pinned model.
- `semantic.py`'s `search()` gains an `include_documents: bool = False` parameter: when set, runs
  a second `<=>` query against the new tables and merges both result sets by distance before
  truncating to `limit` -- an explicit opt-in, so Phase 6's already-verified message-search
  behavior is unchanged by default.
- `cli.py`: `semantic-search` gains `--include-documents`.

## Exit tests

1. `sync` registers `semantic_text_unit_document` and `semantic_embedding_document_bge_small`.
2. A synthetic `records` row in an allowlisted namespace produces exactly one `semantic.
   text_unit_document` row; a synthetic row in a non-allowlisted namespace (e.g. a fake
   `drive/document/csv`) produces none -- the exclusion list is enforced, not just documented.
3. A synthetic record with text over 8,000 chars is stored truncated with `truncated = true`.
4. A record with neither `occurred_at` nor `created_at` set lands in that source's undated
   partition and is still processed (the scheduler fix from item 6's rollout holds for a second,
   independent root deriver, not just the one it was found in).
5. `semantic_embedding_document_bge_small` produces exactly one embedding row per text unit,
   `vector` has 384 dimensions, re-running with no new units is a no-op.
6. `semantic-search(..., include_documents=True)` against a real short corpus containing both a
   message and a document about the same topic returns both, ranked by actual distance, not
   documents-always-first or messages-always-first.
7. Real-database rollout: text units and embeddings computed for the real ~590-row population
   (fewer than the 97,402 raw `records` count once the allowlist and truncation are applied);
   `semantic-search --include-documents` spot-checked against real queries naming content Ry
   knows is in a NotebookLM notebook or a Recorder transcript specifically.
