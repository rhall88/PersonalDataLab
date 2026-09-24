# Phase 6 implementation plan: semantic layer (text units + local embeddings)

`DERIVED_MEANING_ARCHITECTURE.md` section 15 doesn't number this as its own phase -- it's
explicitly cross-cutting ("The semantic layer ... can be built in parallel with phases 4-6") --
and `ROADMAP.md`'s own item 5 names it directly: "Semantic retrieval: deterministic chunking and
locally controlled embeddings as disposable derived data, never as canonical evidence." This plan
scopes that against what Phases 0-5 already computed on the real canonical database. Item 6 (OCR/
transcription/document analysis) is explicitly separate scope in the same roadmap list and is not
touched here. Semantic *annotation* (section 1's SEMANTIC class -- topic labeling via a local
LLM) is section 15's stage 8 alongside embeddings, but needs a second, different kind of local
model (a generative/annotation model, not an embedding model) and a real decision about which one
and how to keep its output from ever masquerading as deterministic measurement (invariant 7) --
deferred to its own later plan rather than bundled in here to hit stage 8's letter.

## Grounding (queried against the real database, 2026-09-24)

- **488,057 events carry non-null `text`**, but the overwhelming majority are event-log noise,
  not conversational content worth embedding: `google.account_access` (104,216 rows, 153 chars
  avg -- login notification text), `apple`'s App Store/Music click-activity event types (100K+
  rows, ~9-13 chars avg -- not prose at all), purchase/download history, and similar. Restricting
  to genuinely conversational/document event types gives a much smaller, real number:

  | Source | event_type | Rows |
  |---|---|---:|
  | facebook | message | 50,574 |
  | google | mail_message | 47,277 |
  | imessages | message.incoming | 35,965 |
  | imessages | message.outgoing | 31,915 |
  | openai | conversation_message | 25,510 |
  | anthropic | conversation_message | 9,673 |

  **200,914 raw messages total.** This is the actual input population for this phase -- nothing
  else in the 488,057 non-null-text events is in scope.
- pgvector is already installed on the real database (`vector` extension, version 0.8.6) --
  confirmed directly, not assumed from `compose.yaml`'s base image. No new Postgres extension
  needed, only new tables.
- Phase 4's own grounding already established (and this plan reuses without re-verifying):
  `google`'s `events.conversation_id` is NULL on every row (0 of 164,063), so google has no
  chat-style session boundary to group by at all; `openai`/`anthropic` already group messages
  into real `conversation_id`s that are themselves natural, pre-existing units (an AI chat
  "conversation" is already the right grain -- there is no reason to invent a second grouping
  rule inside one).
- `derive.metric_dependency`/`METRIC_CATALOG` (Phase 3) already treats `facebook.messages_count`/
  `imessages.*_count` per-day, and Phase 4's `measure.interaction_dynamics` (Phase 4) already
  established which facebook/imessages threads are clean 1:1 conversations (306 facebook, 25
  imessages) -- this plan doesn't reuse that restriction (session grouping applies to *all*
  facebook/imessages conversations, not just clean 1:1 ones; a group thread's messages are still
  real conversational text worth being able to search), it's cited only to confirm
  `conversation_id` is a real, populated, reliable grouping key for both sources.
- No embedding, annotation, or local-model tooling exists anywhere in this codebase yet
  (confirmed: no `embedding`/`pgvector`/model-loading code outside this plan's own grounding
  search). This is a first, not an extension of existing machinery.

## Decisions

1. **Scope: text units + embeddings only. No annotation, no OCR, no query-answering CLI beyond a
   minimal nearest-neighbor search.** Matches the architecture's own explicit separation (OCR is
   roadmap item 6; annotation needs a second, different model and its own invariant-7 discipline)
   and this project's established pattern of shipping the narrowest usable slice per phase.
2. **Three different text-unit construction rules, one per source shape -- not one generic rule
   forced onto all of them (grounded above, not invented):**
   - **facebook `message` / imessages `message.incoming`+`message.outgoing`**: group into
     sessions per `conversation_id` using the architecture's own 45-minute-gap rule (section 15
     stage 8) -- a new message starting more than 45 minutes after the conversation's
     previous message starts a new unit. Reactions/edits/deletions (imessages) and
     comments/reactions (facebook, already excluded from `event_type` scope above) are not text
     content and are not grouped into units.
   - **openai / anthropic `conversation_message`**: one text unit per existing `conversation_id`
     -- the provider's own conversation boundary already is the right grain, per the grounding
     above. No 45-minute regrouping invented for something that already has a real boundary.
   - **google `mail_message`**: one text unit per message. Gmail thread/subject grouping is not
     reliably available in this canonical schema (not verified to exist), and a time-gap
     heuristic doesn't fit email's asynchronous shape at all -- inventing an ungrounded grouping
     rule here would be worse than no grouping. Each email is already a coherent, embeddable unit
     on its own.
   A text unit's `text` field is its constituent messages' text concatenated in `occurred_at`
   order (`"\n\n"`-joined, matching how a reader would actually encounter the exchange), capped at
   a fixed character budget (8,000 chars -- generous for the embedding model's own input window,
   see decision 3) with a `truncated` flag rather than silently dropping the tail.
3. **Embedding model: `BAAI/bge-small-en-v1.5` via `fastembed` (ONNX Runtime), not
   `sentence-transformers`/PyTorch.** `fastembed` runs the model through ONNX Runtime with no
   PyTorch/CUDA stack at all -- a much smaller, CPU-friendly dependency footprint appropriate for
   a project whose only infrastructure requirement so far has been PostgreSQL. `bge-small-en-v1.5`
   is a small (~130MB), MIT-licensed, retrieval-specifically-trained model (unlike the more
   general-purpose `all-MiniLM-L6-v2`), 384 dimensions, well inside the fixed 8,000-char input
   budget from decision 2. Pinned exactly (`fastembed==0.4.*`, model name pinned in code, weights
   content-hashed at load time) so the embedding space -- and therefore every stored vector -- has
   a stable, versioned identity per the architecture's own version-hash model (section 11: "model
   ID and weights hash ... change any of them and it is a new version"). Runs fully locally, no
   network call at inference time, satisfying every source's default `local_only` egress
   (`policy.source_policy`, confirmed all six sources still default there) without needing an
   override.
4. **Disposability: this whole layer can be dropped and rebuilt from canonical evidence with zero
   loss**, per invariant 20 and the same precedent `measure`/`signal`/`episode`/`know` already
   established -- `semantic.text_unit`/`semantic.embedding_bge_small_en_v15` are pure derivations
   from `events`, never canonical evidence themselves, never referenced by a foreign key from any
   canonical table.
5. **Storage: one new schema (`semantic`), one embedding table per embedding space** (the
   architecture's own naming pattern, `embedding_<space>`) -- `semantic.embedding_bge_small_en_v15`
   specifically, so a future model swap adds a second table/HNSW index rather than migrating
   vector width in place. `pgvector`'s `vector(384)` column type, HNSW index (`vector_cosine_ops`
   -- cosine similarity is `bge-small-en-v1.5`'s own recommended metric).
6. **Runtime: two LOCAL/HALO derivers, riding the existing framework unchanged.**
   `semantic_text_unit` (HALO, `lookback_halo="1 days"`/`lookahead_halo="1 days"` -- generous
   enough that a 45-minute session spanning a month boundary is never split by partitioning
   alone, matching `source_activity_7d`'s own halo precedent) reads `events` directly (LOCAL
   source, no upstream deriver dependency -- text units are a root derivation off canonical
   evidence, same tier as `source_activity_daily`/`measure_coverage`). `semantic_embedding_bge_small`
   (LOCAL, `inputs=("semantic_text_unit",)`, 1:1 partition mapping) reads that partition's text
   units and computes their vectors. Splitting construction from embedding means a future new
   embedding space is a new deriver reading the same `semantic.text_unit` rows, not a redo of
   session-grouping.
7. **CLI: `personal-data-lab semantic-search "<query>" [--limit N] [--source ...]`.** Embeds the
   query text with the same pinned model, ranks `semantic.text_unit` by cosine distance, and
   prints each hit's source/conversation/date-range/text with a direct link back to its
   constituent canonical event ids -- the architecture's own rule that vectors only ever fetch
   examples, statistics decide what matters (section 8 step 9), realized here as: no ranking
   claim, no confidence profile, no `know.claim` row from this phase -- semantic search is a
   lookup tool, not a statistical finding.

## Schema (migration 017)

```sql
CREATE SCHEMA IF NOT EXISTS semantic;

CREATE TABLE semantic.text_unit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    conversation_id BIGINT REFERENCES conversations(id),
    event_ids BIGINT[] NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,
    text TEXT NOT NULL,
    truncated BOOLEAN NOT NULL DEFAULT FALSE,
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX text_unit_source_started_idx ON semantic.text_unit (source, started_at);
CREATE INDEX text_unit_conversation_idx ON semantic.text_unit (conversation_id);

CREATE TABLE semantic.embedding_bge_small_en_v15 (
    text_unit_id BIGINT PRIMARY KEY REFERENCES semantic.text_unit(id) ON DELETE CASCADE,
    vector vector(384) NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX embedding_bge_small_en_v15_hnsw_idx
    ON semantic.embedding_bge_small_en_v15
    USING hnsw (vector vector_cosine_ops);
```

No canonical table altered. `event_ids` is a plain array, not a join table -- this phase has no
purge/invalidation machinery to traverse it relationally yet (a real, pre-existing gap this
project hasn't built regardless of this phase; not solved here), and an array is sufficient for
every read this phase's own CLI needs (`claims-why`-style provenance display, not a query that
needs to join *from* an event back to its unit at scale).

## Runtime

- `derive/derivers/semantic.py`: `semantic_text_unit` (HALO, no inputs, `clear_partition` deletes
  this partition's rows from `semantic.text_unit`, which cascades to
  `semantic.embedding_bge_small_en_v15` via `ON DELETE CASCADE`) implements decision 2's three
  construction rules per source. `semantic_embedding_bge_small` (LOCAL, `inputs=
  ("semantic_text_unit",)`) loads the pinned `fastembed` model once per worker process (module-
  level lazy init, not per partition) and embeds every text unit in its partition that doesn't
  already have a row in `semantic.embedding_bge_small_en_v15` -- clear-and-replace per partition
  like every other deriver, but computing an embedding is comparatively expensive, so this is
  the one deriver in the codebase where `clear_partition` deliberately does *not* unconditionally
  wipe rows whose source text unit is unchanged (checked by re-embedding only text units the
  worker's own fingerprint check identifies as new since the last successful run for this
  partition -- ordinary `ComputeResult`/fingerprint behavior already does this for the *partition
  as a whole*; skipping unchanged embedding work *inside* one dirty partition's compute() is a
  local optimization, not new framework behavior).
- `semantic_search.py` (or a function in `semantic.py`): `search(engine, query, *, limit=10,
  source=None) -> list[dict]`, embedding the query with the same pinned model and running a
  pgvector `<=>` (cosine distance) nearest-neighbor query against
  `semantic.embedding_bge_small_en_v15` joined to `semantic.text_unit`.
- `cli.py`: `semantic-search` verb.
- `pyproject.toml`: add `fastembed>=0.4,<0.5`.

## Exit tests

1. `sync` registers `semantic_text_unit` and `semantic_embedding_bge_small` into `derive.deriver`
   alongside every existing deriver.
2. A synthetic facebook conversation with two messages 10 minutes apart, then a third 50 minutes
   after the second, produces exactly two text units (a 2-message unit and a 1-message unit) --
   the 45-minute-gap rule verified against a hand-constructed case, not just real data.
3. A synthetic openai conversation with three messages produces exactly one text unit spanning
   all three, regardless of the gaps between them -- confirming decision 2's "use the provider's
   own conversation boundary" rule for openai/anthropic.
4. A synthetic google mail_message produces exactly one text unit per message, never grouped with
   another.
5. A synthetic long text unit (over the 8,000-char budget) is stored truncated with `truncated =
   true`, never silently dropped or silently untruncated.
6. `semantic_embedding_bge_small` produces exactly one `semantic.embedding_bge_small_en_v15` row
   per text unit, `vector` has exactly 384 dimensions, and re-running the deriver with no new text
   units is a no-op (fingerprint-skipped, no re-embedding cost) -- confirming the framework's
   ordinary skip-unchanged behavior actually holds for this deriver, not just derivers with cheap
   compute().
7. `semantic-search` against a real short corpus returns the hand-verified most-relevant text unit
   first for an unambiguous query (e.g. a query naming a specific, real event only one message
   describes).
8. Real-database rollout: text units and embeddings computed for the real 200,914-message
   population; `semantic-search` spot-checked against at least 5 real queries naming things Ry
   knows are in the corpus (reusing lessons from the 2026-09-24 episode review -- e.g. a query
   about the grandmother's move should surface real messages from that thread).
