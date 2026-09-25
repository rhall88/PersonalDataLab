# Phase 7 implementation plan: semantic annotation (topic labeling)

`PHASE6_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md` decision 1 explicitly scoped Phase 6 to text units
and embeddings only, deferring annotation "to its own later plan rather than bundled in here to
hit stage 8's letter." `ROADMAP.md`'s next-up line names it directly: "Then: OCR/transcription;
semantic annotation/topic-labeling" -- OCR/transcription shipped as item 6 (media transcription,
PR #68, 2026-09-24). This plan is that deferred piece: `DERIVED_MEANING_ARCHITECTURE.md` section 1's
SEMANTIC epistemic class, realized as `signal.annotation` rows over Phase 6's existing
`semantic.text_unit` population -- no new text units, no new embedding space, no OCR/image work.

## Grounding (queried against the real database, 2026-09-25)

- **70,145 `semantic.text_unit` rows already exist** (Phase 6, verified exact 1:1 with their
  embeddings on rollout) -- this phase's entire input population, nothing else:

  | Source | Units | Avg chars | Max chars |
  |---|---:|---:|---:|
  | google | 46,972 | 6,319 | 8,000 |
  | imessages | 12,514 | 375 | 8,000 |
  | facebook | 9,316 | 276 | 8,000 |
  | openai | 1,212 | 5,219 | 8,000 |
  | anthropic | 131 | 5,300 | 8,000 |

  Every unit is already capped at 8,000 chars (Phase 6 decision 2), so this phase's model needs a
  context window comfortably above ~2,000 tokens, not unbounded.
- `signal.annotation` is already named in `DERIVED_MEANING_ARCHITECTURE.md`'s own schema table
  (`unit_id, kind (topic, event_mention, intent), value, score, versions`) -- this plan implements
  that table, not a new one invented for this phase.
- `derive.deriver_version` already has a `model_ref JSONB` column (migration 010, confirmed in
  `db/init/002_schema.sql`), and `versioning.py` already folds `model_ref` into the deriver's
  `version_hash` alongside `code_version`/`params` -- the framework's "change the model and it's a
  new version" behavior (architecture section 4) requires zero framework changes. This phase is the
  first deriver to actually populate `model_ref` with a real model identity.
- `final_inference="SEMANTIC"` is defined in `registry.py`'s `EPISTEMIC_CLASSES` but has never
  been used by any deriver yet (grepped every `derivers/*.py`) -- every deriver so far has been
  COMPUTED. This phase is the first real SEMANTIC-class output in the codebase, which is exactly
  why invariant 7 (never let a semantic output masquerade as deterministic measurement) gets its
  own explicit handling below rather than inheriting COMPUTED's implicit trust.
- No LLM/generative-model tooling exists anywhere in this codebase yet (confirmed: no
  `llama`/`gguf`/`transformers`/`torch` reference outside this plan's own grounding search) --
  `fastembed` (Phase 6) and `faster-whisper` (item 6) are both non-generative, so this is a new
  category of local model for the project, not an extension of either.
- **C: drive has 24.8GB free** as of 2026-09-25 (`Get-PSDrive C`), independently confirmed via the
  same disk-pressure conversation that's driving the separate external-hard-drive migration for
  `DataLabData`. Any model weights this phase downloads compete directly with that remaining
  headroom -- see decision 6.

## Decisions

1. **Scope: topic labeling only (`kind = 'topic'`). No `event_mention`, no `intent`.** Matches
   Phase 6's own "narrowest usable slice per phase" discipline and the architecture's own listing
   of `annotation.kind` as three independent values needing no shared machinery beyond the table
   itself -- `event_mention` and `intent` are each their own future phase, not partial work left
   in this one.
2. **Closed label set, enforced by constrained generation, not free text.** Architecture line
   "Closed label sets where possible" (section on semantic annotation quality controls) is taken
   literally: `llama.cpp` supports GBNF grammars that constrain a model's output to a fixed
   enumeration at the token-sampling level, not via prompt instruction the model might ignore. A
   starter taxonomy of ~18 topic labels (family, health, finances, work, relationships/dating,
   logistics/scheduling, technology, travel, creative projects, news/politics, food, housing,
   legal, education, entertainment, shopping, pets, other) plus `score` (the model's own token
   probability for the label it picked, not a second LLM call asking "how confident are you") is
   grammar-enforced, so parsing failure is structurally impossible rather than caught after the
   fact. The taxonomy itself is a versioned constant in code (`_TOPIC_LABELS` tuple) -- changing it
   changes `params`, which changes `version_hash`, which is a new deriver version per the
   framework's existing model.
3. **Model: a GGUF-quantized instruct model run through `llama-cpp-python` (`llama.cpp`), not
   PyTorch/`transformers`.** Continues this project's established pattern (`fastembed`'s ONNX
   Runtime for Phase 6, `faster-whisper`'s CTranslate2 for item 6) of avoiding the PyTorch/CUDA
   stack entirely for a project whose only infrastructure requirement has been PostgreSQL.
   Specifically: **Phi-3.5-mini-instruct** (Microsoft, MIT-licensed, ~3.8B params, 128K native
   context window -- far more than this phase's ~2,000-token ceiling needs, which matters because
   headroom means the fixed prompt template plus a full 8,000-char unit never risks truncation),
   `Q4_K_M` GGUF quantization (~2.2GB on disk). Runs fully locally, CPU-only, no network call at
   inference time -- satisfies every source's default `local_only` egress
   (`policy.source_policy`) the same way Phases 6 and item 6 already do.
4. **Model identity is pinned and hashed into `model_ref`, not just named in a docstring.**
   `model_ref = {"model_id": "microsoft/Phi-3.5-mini-instruct-GGUF", "quantization": "Q4_K_M",
   "weights_sha256": "<computed at load time from the actual .gguf file>", "prompt_hash":
   "<sha256 of the fixed prompt template>", "topic_labels_hash": "<sha256 of the taxonomy
   tuple>"}`. Any of these changing -- a re-quantized file, an edited prompt, an added label --
   produces a different `version_hash` and therefore a new `deriver_version`, triggering
   re-annotation under the framework's existing dirty-tracking, exactly the "model and prompt
   pinned" requirement the architecture states for this class of output.
5. **Every `signal.annotation` row is unambiguously reproducible-or-not, per invariant 7.** A
   SEMANTIC output is never treated as ground truth elsewhere in the schema: `signal.annotation`
   carries no foreign key *from* any canonical table, only a reference *to* `semantic.text_unit`,
   mirroring Phase 6's own `semantic.embedding_bge_small_en_v15` -> `semantic.text_unit`
   direction. `score` stores the model's own label-token probability (a real number the model
   actually computed, not a fabricated confidence), and nothing downstream (there is no downstream
   consumer yet -- `know.claim` templates that would cite an annotation are a later phase, per
   `claim_evidence`'s `evidence_ref` already listing `annotation` as a valid reference kind) may
   treat a topic label as an assertion of fact rather than "a local model's reading of this text."
6. **Model download deferred; schema and runtime land in this phase, inference does not run
   against the canonical database yet.** C: has 24.8GB free (grounding above) and a real
   external-drive migration for `DataLabData` is already in progress in parallel. Downloading a
   2.2GB model file right now competes with that headroom for no urgent reason. This PR ships the
   migration, the deriver, and a real (not mocked) end-to-end test against a small,
   openly-licensed *test-only* GGUF model (tens of MB, e.g. one of the standard tiny
   llama.cpp-ecosystem smoke-test models) so the actual inference path -- grammar-constrained
   generation, row shape, `model_ref` hashing -- is proven end to end the same way
   `test_transcribe_one_real_whisper_call_on_a_tiny_synthetic_file` proved item 6's chain, without
   requiring the production model on disk. Pointing `_MODEL_PATH`/`_MODEL_URL` at the real
   Phi-3.5-mini-instruct GGUF and running the real rollout against canonical is a follow-up PR,
   explicitly gated on the external-drive move freeing up headroom (or an explicit go-ahead to eat
   2.2GB of C: now).
7. **Runtime: one new LOCAL deriver, riding the existing framework unchanged**, exactly Phase 6's
   `semantic_embedding_bge_small` shape: `signal_annotation_topic` (LOCAL, `inputs=
   ("semantic_text_unit",)`, 1:1 partition mapping, `final_inference="SEMANTIC"` -- the codebase's
   first use of that class) loads the pinned model once per worker process (module-level lazy
   init) and annotates every text unit in its partition lacking a `signal.annotation` row for this
   deriver version, clear-and-replace per partition like every other deriver.

## Schema (migration 019)

```sql
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
```

`kind` allows all three architecture-defined values even though this phase only ever writes
`'topic'` -- the CHECK constraint documents the eventual shape without inventing rows this phase
doesn't produce. `UNIQUE (text_unit_id, kind)` matches `clear_partition`'s delete-and-replace
model: one current topic label per unit, never an accumulating history row (superseded rows are
simply deleted, same as every other deriver's output_relation in this codebase).

## Runtime

- `derive/derivers/annotation.py` (new file, not added to `signal.py` -- `signal.py` already
  holds baseline/change_point/anomaly, which are STATISTICAL, not SEMANTIC; keeping the first
  SEMANTIC deriver in its own module makes that epistemic-class boundary visible in the file
  layout, not just in `final_inference`'s string value):
  - `_TOPIC_LABELS: tuple[str, ...]` -- the pinned 18-label taxonomy from decision 2.
  - `_build_grammar() -> str` -- a GBNF grammar restricting output to exactly one of
    `_TOPIC_LABELS`, built once at module load from the tuple (so the taxonomy has one source of
    truth, not a taxonomy list plus a hand-written duplicate grammar string).
  - `_get_model()` -- module-level lazy singleton loading `Llama(model_path=..., n_ctx=4096)` from
    `llama_cpp`, mirroring `media.py`'s `_get_model()` and `semantic.py`'s embedding-model init.
  - `_annotate_one(text_unit_row) -> dict` -- runs the fixed prompt template with the grammar,
    reads the label and its token logprob back out, returns a `signal.annotation` row.
  - `@deriver(name="signal_annotation_topic", partition_class="LOCAL", final_inference="SEMANTIC",
    output_relation="signal.annotation", inputs=("semantic_text_unit",), model_ref={...})`.
- `pyproject.toml`: add `llama-cpp-python>=0.3,<0.4` (prebuilt CPU wheels, no CUDA toolchain
  needed at install time, matching `fastembed`/`faster-whisper`'s zero-extra-system-dependency
  bar).

## Exit tests

1. `sync` registers `signal_annotation_topic` into `derive.deriver` alongside every existing
   deriver, with `final_inference = 'SEMANTIC'` -- the first row in `derive.deriver` ever to have
   that value, asserted explicitly (not just "registration succeeds") so a future refactor can't
   silently regress the codebase's first real use of the SEMANTIC class.
2. `_build_grammar()` produces a grammar that, run through the real (tiny test) model, never
   returns a label outside `_TOPIC_LABELS` across a small batch of varied synthetic inputs --
   proving the constraint is real (token-level), not just a prompt suggestion the model happened
   to follow on lucky inputs.
3. A synthetic `semantic.text_unit` row produces exactly one `signal.annotation` row with `kind =
   'topic'`, `value` in `_TOPIC_LABELS`, and `score` in `[0, 1]`.
4. `model_ref`'s `weights_sha256` changes (and therefore `version_hash` changes, and therefore the
   partition is re-dirtied) when the test model file's bytes change -- a real hash computed from a
   real file, not a hardcoded string, proving the "swap the model, get a new version" claim
   actually holds in this deriver rather than just in the framework's generic machinery already
   tested elsewhere.
5. Re-running the deriver with no new text units and an unchanged `model_ref` is a no-op
   (fingerprint-skipped) -- confirming the framework's ordinary skip-unchanged behavior holds here
   too, same rigor Phase 6 demanded of `semantic_embedding_bge_small`.
6. `clear_partition` for source/month X only deletes `signal.annotation` rows whose
   `text_unit_id` belongs to a `semantic.text_unit` in that source/month, never another
   partition's rows -- same isolation test pattern as `test_clear_partition_only_deletes_its_own_
   source_and_month` in `tests/test_media.py`.

Real-database rollout (70,145 real text units annotated with the real Phi-3.5-mini-instruct
model) is explicitly **not** an exit criterion of this PR, per decision 6 -- it is the follow-up
PR's own exit test once the model download is unblocked.
