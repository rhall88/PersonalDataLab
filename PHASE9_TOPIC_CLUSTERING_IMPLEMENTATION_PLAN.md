# Phase 9 implementation plan: unsupervised topic clustering

`DERIVED_MEANING_ARCHITECTURE.md` names this explicitly, twice: the GLOBAL-partition-class
examples table lists "topic clustering" alongside PELT segmentation and Splink identity
clustering, and section 5's entity taxonomy has a dedicated rule for it: **"Topics are not
entities... Machine-discovered topic → versioned semantic annotation... A new embedding model can
reorganize them completely."** Phase 7 (`signal_annotation_topic`) already delivers one kind of
topic label -- a fixed 18-category taxonomy, chosen in advance, applied by an LLM. This phase
delivers the other kind the architecture anticipated: labels *discovered* from the corpus's own
structure, not chosen ahead of time. The two are complementary, not competing: Phase 7 answers
"which of these known categories," this phase answers "what groupings actually exist in the data
that no fixed taxonomy would have anticipated" -- the manuscript-in-progress, the specific job
search, the recurring argument, none of which "family" or "work" alone would surface.

## Why this phase needs no new inference pass

Every other item in the semantic layer (Phase 6 embeddings, Phase 7 annotation, Phase 8 document
embeddings) required running a pinned model over the corpus -- the actual bottleneck this session
hit repeatedly (CPU infeasibility, GPU rental, host GPU-passthrough defects). Clustering needs
none of that: it is pure linear algebra over the 70,145 embedding vectors Phase 6 already computed
and persisted in `semantic.embedding_bge_small_en_v15` (confirmed still 70,145/70,145, exact 1:1,
against the real canonical database at plan time; `pgvector` 0.8.6 is the installed extension
version). This is deliberately the cheapest, highest-value-per-effort item on the current
roadmap's "what's next" list -- CPU-only, no model download, minutes not hours, runnable on this
dev machine with no rented hardware at all.

## Decisions

1. **Messages only, in this phase -- not documents.** `semantic.embedding_bge_small_en_v15` (70,145
   rows, Phase 6) is in scope; `semantic.embedding_bge_small_en_v15_document` (597 rows, Phase 8)
   is not. Same reasoning Phase 6/7/8 already established for sequencing: ship the large,
   well-understood population first, fold the smaller document population in as an explicit
   follow-up once this shape is proven. A `document_id`-tagged variant is a natural Phase 9b, not
   this phase's job.
2. **Algorithm: HDBSCAN, not k-means.** k-means forces every point into a cluster and requires
   choosing k in advance -- both wrong for "what groupings genuinely exist," which is exactly what
   this phase wants to discover rather than assume. HDBSCAN finds a variable number of
   variable-density clusters and explicitly labels low-density points as noise (`cluster_index =
   -1`) rather than forcing them into a nearest cluster that doesn't really fit -- consistent with
   invariant 2's "don't invent value where none exists" discipline already applied throughout this
   project (Phase 8's nullable timestamps, Phase 5's honest small-N claim population). Implemented
   via `sklearn.cluster.HDBSCAN` (scikit-learn >= 1.3), one new dependency, CPU-only, no GPU/CUDA --
   continuing this project's established avoidance of the PyTorch/CUDA stack (`fastembed`'s ONNX
   Runtime, `faster-whisper`'s CTranslate2, `llama-cpp-python`'s CPU build all made the same
   choice).
3. **Cosine geometry via L2-normalized vectors + Euclidean HDBSCAN.** `bge-small-en-v1.5`'s own
   recommended metric is cosine (Phase 6 decision 5, already reflected in the `hnsw
   vector_cosine_ops` index). HDBSCAN's reference implementation works over Euclidean distance;
   for unit-normalized vectors, Euclidean distance is a monotonic function of cosine distance
   (`||a-b||^2 = 2 - 2*cos(a,b)` when `||a||=||b||=1`), so L2-normalizing every vector before
   clustering makes Euclidean HDBSCAN produce the same neighborhood structure cosine similarity
   would -- a documented equivalence, not an approximation, and it avoids a second clustering
   backend just to get a metric HDBSCAN doesn't take directly.
4. **Cluster labels are extractive, not generative -- deterministic TF-IDF top terms, never an LLM
   call.** The architecture is explicit that an LLM-suggested label is a HYPOTHESIS, and this
   phase's whole point is to stay in the cheap, fully-reproducible, no-inference-pass category. A
   cluster's `label` is the top 5 terms by TF-IDF score computed *within that cluster's own text
   units against the whole corpus as the background distribution* (`sklearn.feature_extraction
   .text.TfidfVectorizer`, English stopwords, no new dependency beyond scikit-learn itself), joined
   with commas (e.g. `"lease, landlord, deposit, apartment, move-in"`). Deterministic given the
   same cluster membership -- no sampling, no temperature, nothing to pin as `model_ref`.
5. **`final_inference = "STATISTICAL"`, not `"SEMANTIC"`.** The architecture's own class table
   draws this line precisely: STATISTICAL is "a statistical inference over measurements" (its
   worked example is a change point over weekly volume); SEMANTIC is "a semantic model says this."
   HDBSCAN clustering over embeddings is a deterministic statistical procedure given a fixed random
   seed and fixed input vectors -- it does not itself interpret meaning, Phase 6's embedding model
   already did that upstream (`depends_on` will include Phase 6's own COMPUTED class, matching the
   existing `signal_change_point`/`signal_anomaly` precedent of STATISTICAL final inference over
   COMPUTED metric inputs). The TF-IDF label in decision 4 is extractive term-frequency math, not a
   model's semantic judgment either, so it doesn't push the class to SEMANTIC.
6. **One new GLOBAL deriver, not LOCAL/HALO.** Clustering fundamentally needs the whole embedding
   population at once -- there is no meaningful per-partition (per source-month) clustering that
   wouldn't just be an arbitrary chop of one global structure. This matches the architecture's own
   GLOBAL-class example list verbatim ("topic clustering" is named there) and follows
   `know_claim_evaluate`/`episode_detect_candidates`'s existing GLOBAL precedent: one partition key
   (`"GLOBAL"`), recomputed whole-corpus on every dirty run.
7. **Versioned and fully disposable, per invariant 20.** `semantic.topic_cluster_assignment` rows
   carry `deriver_version_id` (not a bare run timestamp) exactly like every other derived table in
   this project. A re-cluster (new data, a bumped `code_version` for an algorithm/parameter change,
   or a future embedding-model swap) produces a new deriver version; per `versioning.py`'s existing
   shadow/active/promote mechanism, it does not overwrite the active clustering until explicitly
   promoted. `clear_partition` deletes this version's own `topic_cluster_assignment` rows only,
   same isolation discipline every other deriver's `clear_partition` already has test
   coverage for.
8. **No `named_concept` entity promotion in this phase.** The architecture describes a person
   *naming and anchoring* a discovered cluster into a persistent `named_concept` entity that
   survives re-clustering (e.g. "The Hallway," "PersonalDataLab" as things you declared matter,
   not just statistical groupings). That is real, valuable, future work explicitly deferred here --
   it needs its own review/confirm UI verb (the same shape `episode-confirm` added for episodes),
   which doesn't exist yet and is out of scope for a CPU-only backend phase. This phase ships the
   machine-discovered layer only; anchoring is Phase 9's own natural follow-up, not bundled in.

## Schema (migration 022)

**Revision note, caught before implementation:** the original two-table design (a `topic_cluster`
metadata table plus a `topic_cluster_assignment` row-per-unit table) would need one `compute()` to
write both -- exactly the constraint Phase 5's inline-JSONB correction and Phase 10's candidate/
confirm pattern both exist to work around (`derive/worker.py`'s `_write_rows` inserts into exactly
one `output_relation`). Fixed the same way Phase 5 fixed it: one table, with the cluster-level
label denormalized onto every member row rather than factored into a separate table. A cluster's
centroid is not persisted at all -- nothing in this phase's exit tests needs it stored, and it's
cheaply recomputable on demand (`AVG` over `semantic.embedding_bge_small_en_v15.vector` for a
cluster's members) if a future consumer wants it, rather than carrying a redundant 384-dim vector
on every one of ~70,145 rows.

```sql
CREATE TABLE semantic.topic_cluster_assignment (
    text_unit_id BIGINT NOT NULL REFERENCES semantic.text_unit(id) ON DELETE CASCADE,
    deriver_version_id BIGINT NOT NULL REFERENCES derive.deriver_version(id),
    cluster_index INTEGER NOT NULL,  -- -1 is HDBSCAN's reserved "noise" label, never a real topic
    cluster_label TEXT NOT NULL,     -- top-5 TF-IDF terms, comma-joined; 'noise' for cluster_index=-1
                                      -- (denormalized: identical for every row sharing cluster_index)
    distance_to_centroid DOUBLE PRECISION,  -- NULL for noise-labeled units (no centroid to measure)
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (text_unit_id, deriver_version_id)
);

CREATE INDEX topic_cluster_assignment_cluster_idx
    ON semantic.topic_cluster_assignment (deriver_version_id, cluster_index);
```

The primary key is `(text_unit_id, deriver_version_id)`, not `text_unit_id` alone, for the same
reason the original design's composite key was: a shadow version's rows and the active version's
rows can coexist before an explicit `derive promote`, exactly `derive.partition_state`'s own
existing multi-version precedent. "What clusters exist" (decision 4's CLI) is answered by a plain
`GROUP BY cluster_index, cluster_label` query, not a second table.

## Runtime

- `derive/derivers/clustering.py` (new file, its own module -- distinct epistemic territory from
  `annotation.py`'s SEMANTIC-class LLM work, matching that file's own precedent of isolating a new
  inference class into its own module rather than folding it into `semantic.py`):
  - `_fetch_vectors(connection) -> tuple[list[int], np.ndarray]` -- reads every
    `(text_unit_id, vector)` pair from `semantic.embedding_bge_small_en_v15`, L2-normalizes each
    row (decision 3).
  - `_cluster(vectors) -> np.ndarray` -- `HDBSCAN(metric="euclidean", min_cluster_size=...)` (an
    explicit `min_cluster_size`/`min_samples` pair recorded in `params`, grounded against the real
    70,145-vector population before being fixed, not guessed -- this plan does not pre-commit a
    number it hasn't checked produces a sane cluster-count/noise-ratio on the real data).
  - `_label_cluster(texts_in_cluster, all_texts) -> str` -- the TF-IDF top-5 extraction (decision 4).
  - `@deriver(name="semantic_topic_cluster", partition_class="GLOBAL", final_inference="STATISTICAL",
    output_relation="semantic.topic_cluster_assignment", inputs=("semantic_embedding_bge_small",),
    params={"algorithm": "hdbscan", "min_cluster_size": ..., "min_samples": ..., "metric":
    "euclidean_on_l2_normalized"}, clear_partition=...)` -- `params` (not `model_ref`, which stays
    reserved for pinned model identity per Phase 7's own precedent) carries every knob that changes
    `version_hash` when tuned.
  - `compute()`: fetch all vectors, cluster, compute each cluster's TF-IDF label once, then return
    one `topic_cluster_assignment` row per text unit (label denormalized onto every member row of
    its cluster) as the `ComputeResult` -- a single output relation, per the schema revision above.
- `pyproject.toml`: add `scikit-learn>=1.3,<2` (ships `sklearn.cluster.HDBSCAN` and
  `TfidfVectorizer`; no GPU/CUDA extra, matching this project's existing CPU-only ML dependencies).

## CLI

- `topic-clusters-list [--min-size N]` -- every cluster from the active version, ordered by size
  descending, printing `cluster_index`, `label`, `size`.
- `topic-cluster-show <cluster_index>` -- the cluster's label/size plus a sample of its member text
  units' first ~200 characters each, for a human sanity check without a direct SQL query -- the
  same "make the evidence inspectable from the CLI, not just the schema" precedent `claims-why` and
  `episode-list` already set.

## Exit tests

1. `sync` registers `semantic_topic_cluster` with `final_inference = 'STATISTICAL'` and
   `partition_class = 'GLOBAL'`.
2. A synthetic set of embeddings with two obviously separated blobs (plus a few scattered outlier
   points) clusters into exactly two real clusters plus a non-empty noise group -- proving the
   pipeline finds real structure, not that it merely runs without error.
3. L2-normalization + Euclidean HDBSCAN produces the same cluster membership as cosine HDBSCAN
   would, verified directly against a small synthetic case (decision 3's equivalence claim checked,
   not just asserted in prose).
4. `_label_cluster` on a synthetic cluster whose texts share an obvious repeated term (e.g. every
   text unit mentions "lease") includes that term in the returned label; a cluster with no shared
   vocabulary does not fabricate one.
5. Re-running with unchanged embeddings and unchanged `params` is a no-op (fingerprint-skipped),
   matching every other deriver's existing skip-unchanged behavior.
6. `clear_partition` for one deriver version's clustering deletes only that version's
   `topic_cluster_assignment` rows, never another (shadow-vs-active) version's -- same isolation
   test pattern every prior phase's `clear_partition` coverage already established.
7. Noise-labeled (`cluster_index = -1`) units get a `topic_cluster_assignment` row with
   `distance_to_centroid = NULL` -- proving "no real cluster fits this" is represented honestly,
   never forced into the nearest real cluster (invariant 2 applied to this phase's own output, not
   just its inputs).

Real-database rollout (clustering the actual 70,145 real embeddings, and grounding the real
`min_cluster_size`/`min_samples` choice against them before locking `params`) is this phase's own
exit criterion once the schema/runtime PR lands -- unlike Phases 7/8, there is no model download or
rented hardware gating it, so nothing here needs to wait on the media-transcription or annotation
jobs the way those two did. It can run immediately after merge, entirely on this dev machine, in
minutes.
