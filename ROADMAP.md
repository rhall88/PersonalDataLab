# PersonalDataLab roadmap toward complete source readiness

PersonalDataLab develops in four layers. Each layer depends on the evidence guarantees beneath
it: acquire and preserve first, normalize second, derive understanding third, and expose that
understanding to products and agents last.

The audited implementation baseline, merged PR state, schema, and verification summary are in
[`PROJECT_STATE.md`](PROJECT_STATE.md). This roadmap is the authority for current sequencing;
implementation and acceptance reports preserve the state and “next frontier” at their publication
time.

## 1. Google operational acceptance — completed in PR13

The PR5–PR11 implementation covers the 68-product contract and routes every retained Takeout
member. PR13 completed the separate operational gate:

1. import the complete retained Takeout into a fresh production-shaped local database;
2. reconcile all discovered members, bytes, normalized objects, assets, and relationships;
3. interrupt and resume a controlled run without losing completed member transactions;
4. repeat the complete unchanged import;
5. prove that the second run creates no unintended canonical objects or versions;
6. record bounded-memory, elapsed-time, database-size, backup, and restore evidence;
7. publish only sanitized counts and operational results.

PR13 completed the full read, restart/resume proof, clean differential replay of every discovered
conflict, and identical replay of the corrected affected set. See
[`PR13_GOOGLE_PRODUCTION_ACCEPTANCE_REPORT.md`](PR13_GOOGLE_PRODUCTION_ACCEPTANCE_REPORT.md).
The retained 51 present products are explicitly dispositioned as 43 fully structured, six empty,
one asset-indexed, and one partially structured (Nest); no unknown, generic-fallback,
raw-preserved, or unsupported member remains. The other 17 registry products were absent and are
not claimed as empirically validated.

### Nest semantic gap closed 2026-09-20

PR13's Nest disposition retained 31 of 35 real rows as explicit opaque text because they failed
both direct and CSV-unwrapped JSON decoding. The actual cause was a provider encoding bug: Nest
doubly JSON-escapes a nested document, using one extra layer of backslash-escaped quotes
(`\\"` instead of `\"`), which prematurely closes the enclosing JSON string. Halving that one
specific doubled sequence — only as a fallback after both existing decode attempts fail, and only
accepted when the full row then parses cleanly — recovers all 31 rows' real structured content.
A row that still fails to parse after this recovery remains an explicit opaque record with a
warning, exactly as before; nothing is silently repaired. Nest moves from `partially_structured` to
`fully_structured`. The `asset_indexed` Wallet surface (one content-addressed PDF) needed no change:
it was already the correct, final disposition for a single presentation document, not a gap.

This changed decoded content for real rows already present in the canonical database from an
earlier import. Reprocessing required bumping `AccountCommerceStructuredAdapter`'s parser version
first — the restart-safe run-key includes it, so the initial re-import attempt short-circuited
every member without picking up the fix at all. With the version bumped, the re-import against the
canonical database completed with exit 0 across all 25,427 members, zero failures/unknowns/
conflicts: `nest_event` rows went from 35 (31 opaque) to 66, minting 31 new canonical events for the
recovered content rather than editing the original opaque ones in place, consistent with the
immutable-version contract that superseding content is never deletion. This re-import was not
followed by a second idempotency-verification pass, unlike Apple, the original Google import, and
Facebook — deferred by explicit instruction as low priority, given the fix's narrow scope and
existing direct/regression test coverage.

## 2. Add source families without mixing provenance

### Official Apple privacy export — production-ready for the retained corpus

The retained export now has recursive inventory, a 32-surface registry, complete physical versus
logical byte accounting, privacy-safe progress, structured and native-format adapters, streamed
external assets, restart-safe transactions, and coverage reporting. A complete real-data
PostgreSQL import, controlled resume, and exact unchanged replay passed for parser v2. Review
found defects outside exact-replay coverage; parser v3 fixed them. PR16 completed a fresh
real-corpus import, controlled resume, semantic reconciliation, a real empty-TestFlight repair,
and exact unchanged replay. The production-readiness change formalizes lossless CSV/JSON/XML as a
representation contract and reports interpretation depth separately. All 30 present product
groups now pass the ingestion gate, 27 remain visibly `losslessly_structured` / `generic_lossless`,
and complete replay exits 0. Do not infer support for products or formats absent from the retained export. See
[`APPLE_PRODUCTION_READINESS_ACCEPTANCE_REPORT.md`](APPLE_PRODUCTION_READINESS_ACCEPTANCE_REPORT.md)
and [`PR16_APPLE_V3_PRODUCTION_ACCEPTANCE_REPORT.md`](PR16_APPLE_V3_PRODUCTION_ACCEPTANCE_REPORT.md).

### iMessages device export — production-ready in PR15

The retained iMazing CSV source now has complete inventory, privacy-safe progress, explicit
timezone handling, stable duplicate-safe identity, conversations, participants, messages,
replies, reactions and removals, edits, deletions, and honest reference-only attachment assets.
A complete real-data PostgreSQL import, controlled stop/resume, and exact unchanged replay passed.
It remains separate from the official Apple privacy-export provenance family. RSMF and raw iOS
database support are not claimed because those formats were not present.

### Official OpenAI export — production-ready in PR17

The retained manifest-based export now has complete physical/logical accounting, sharded
conversation ingestion, conversation-scoped mapping identities, parent and asset relationships,
structured JSON preservation, external binary assets, explicit unavailable-asset placeholders,
restart-safe transactions, and exact unchanged replay. A complete real-data PostgreSQL import,
controlled stop/resume, production-defect repair, and exact replay passed with exit code 0. See
[`PR17_OPENAI_PRODUCTION_ACCEPTANCE_REPORT.md`](PR17_OPENAI_PRODUCTION_ACCEPTANCE_REPORT.md).

### Anthropic v2 — production-ready in PR19

The v2 production path now supplies privacy-safe manifest/archive/member inventory, complete byte
disposition, deterministic conversation chunks, restart-safe persistence, source-backed and
reference-only assets, and provider-evidenced relationships. Its `anthropic-v2` run identity is
separate from historical `anthropic-v1` runs. Synthetic coverage spans all five observed manifest
categories, private output, unsafe and undeclared inputs, graph construction, and interruption /
resume / replay behavior.

Fresh retained-corpus acceptance then reconciled six source files and eight members into 269
conversations, 9,748 events, 601 explicit unavailable assets, and 10,014 relationships. A controlled
stop after a committed chunk, resume, and exact replay passed with exit 0; all 18 bounded units
short-circuited without count or version drift. See [`ANTHROPIC_INGESTION_SPEC.md`](ANTHROPIC_INGESTION_SPEC.md)
and [`PR19_ANTHROPIC_PRODUCTION_ACCEPTANCE_REPORT.md`](PR19_ANTHROPIC_PRODUCTION_ACCEPTANCE_REPORT.md).

### Canonical database rebuild — Apple and Google imported 2026-09-19

All five accepted source families (Anthropic, OpenAI, iMessages, Apple, Google) now coexist in one
durable canonical `personaldata` database on a dedicated Docker volume, imported directly from raw
exports by the current importer code rather than assembled from historical acceptance databases.
Apple resumed cleanly after an unrelated local interruption to reach its accepted 897-run baseline;
Google ran as one fresh complete pass over the full 146.25 GB retained export with zero failed or
unknown members. See [`PROJECT_STATE.md`](PROJECT_STATE.md) and [`DATABASE_STATE.md`](DATABASE_STATE.md)
for the exact counts. A second identical-replay pass against this combined canonical database has
since confirmed zero drift for both: Apple's 814/814 members and Google's 25,427/25,427 members all
short-circuited, both exiting 0.

### Official Facebook export — production-ready for the retained corpus

A new `facebook_export` module covers all 74 observed product categories across the retained
"Download Your Information" export: bespoke Messenger-thread conversations, content-addressed
media assets (linked back to their thread when one applies), and a generic lossless JSON adapter
for every other category. Three defects surfaced only by real corpus data were found and fixed
before acceptance: PostgreSQL's rejection of embedded NUL bytes, a cross-thread event-identity
collision, and false conflicts from duplicate media/multi-part threads leaking occurrence-specific
data into fields meant to stay stable per canonical identity. A complete real-data import and exact
unchanged replay both passed with exit 0 and `production_ready: true`; see
[`FACEBOOK_PRODUCTION_ACCEPTANCE_REPORT.md`](FACEBOOK_PRODUCTION_ACCEPTANCE_REPORT.md). It was then
imported into the canonical database alongside the other five sources, with a second identical
pass confirming zero drift: all six source families now coexist in one durable `personaldata`
database.

Add later providers only when source evidence exists; every provider starts with inventory and
explicit disposition rather than optimistic format assumptions.

## 3. Build query and understanding layers

1. ~~**Unified full-text query:** searchable conversations, events, records, document text, and
   asset metadata with direct provenance inspection.~~ **Done 2026-09-20** (schema version 8):
   `search_vector`/GIN/trigger on all four canonical tables, ranked `personal-data-lab search`
   command spanning all six sources, zero importer changes required. Applied to the real canonical
   database the same day and verified with real queries — see `PROJECT_STATE.md`. See
   [`QUERY_SPEC.md`](QUERY_SPEC.md).
2. ~~**Identity resolution:** reversible evidence-backed links among provider identities, people,
   accounts, and devices, with manual merge and split controls.~~ **Done 2026-09-20** (schema
   version 9): a purpose-built `identity_links` table (never the dormant, unversioned `identities`
   scaffolding) records evidence-checked, human-asserted merges; `records.actor_person_id` was
   added to match the existing unused `events.actor_person_id`, and both are backfilled/cleared by
   five new `personal-data-lab identity-*` commands. Every merge is validated against real
   `actor_identity` evidence and is fully reversible via split, which preserves history rather than
   deleting it. Applied to the real canonical database the same day and verified with a real
   merge/split/re-merge — see `PROJECT_STATE.md`. See
   [`IDENTITY_RESOLUTION_SPEC.md`](IDENTITY_RESOLUTION_SPEC.md).
3. **Derived entities and claims:** versioned, confidence-scored assertions about people,
   organizations, places, projects, topics, documents, and relationships. Every assertion must
   cite immutable observations and remain removable without changing source truth.
4. ~~**Event correlation:** link observations that may describe the same real-world occurrence
   without deleting or merging the original provider surfaces.~~ **Done 2026-09-22** (schema
   version 14, via [`DERIVED_MEANING_ARCHITECTURE.md`](DERIVED_MEANING_ARCHITECTURE.md)'s Phase 4):
   `signal.change_point`/`signal.anomaly` per-metric detection over Phase 3's metric catalog,
   feeding `episode.episode`'s cross-source change-point clustering into `detected` episode
   candidates — real cross-source correlation the individual providers never expressed on their
   own (9 real candidates on the canonical database, spanning 2016–2026, each clustering change
   points across multiple source families). Also delivered `measure.interaction_dynamics`/
   `signal.interaction_state` for per-correspondent communication-pattern segmentation. See
   `PROJECT_STATE.md`'s Phase 4 rollout section.
5. ~~**Semantic retrieval:** deterministic chunking and locally controlled embeddings as
   disposable derived data, never as canonical evidence.~~ **Done 2026-09-24** (schema version 17,
   `semantic.text_unit`/`semantic.embedding_bge_small_en_v15`): 70,145 real text units and
   70,145 real embeddings against the real canonical database, exact 1:1 match independently
   verified, `semantic-search` CLI live. See
   [`PHASE6_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md`](PHASE6_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md)
   and `PROJECT_STATE.md`'s "Phase 6 semantic layer: rollout" for full evidence.
6. **Optional media enrichment:** OCR, transcription, and document/image analysis only in a
   separately versioned derived layer. Core ingestion never fabricates these results.
7. ~~**Unsupervised topic clustering:** machine-discovered groupings over Phase 6's own
   embeddings — the architecture's own "topics are not entities" rule and its GLOBAL-class example
   list both name this directly. Complements item 6's Phase 7 fixed-taxonomy annotation rather than
   duplicating it.~~ **Done 2026-09-28**: HDBSCAN parameters grounded against a real timed
   benchmark (six combinations swept over the real 70,145-vector population), a real production bug
   found and fixed along the way (the deriver's own clustering call was missing the `n_jobs`
   parallelism the benchmark itself required — a real rollout attempt sat idle for 10+ minutes
   before being caught), rolled out against the real canonical database: 397 real groups (396
   clusters + noise), exact 1:1 row count. See
   [`PHASE9_TOPIC_CLUSTERING_IMPLEMENTATION_PLAN.md`](PHASE9_TOPIC_CLUSTERING_IMPLEMENTATION_PLAN.md)
   and `PROJECT_STATE.md`'s "Phase 9" rollout section.
8. **The life graph:** entities, relationships, events, decisions, and open loops via one
   consolidated generative pass over Phase 6's text units, reusing Phase 7's rented-GPU
   infrastructure. Spec (v3, three review rounds) published 2026-09-26; schema, five deriver
   modules, 15 CLI verbs, and 11 exit tests implemented and passing 2026-09-28 — see
   `PROJECT_STATE.md`'s "Phase 10" section for the real benchmark that grounded its grammar/timing
   (~7.4 hours for the real pass, not the 7-15 days a naive extrapolation would have suggested) and
   four real bugs the benchmark and exit tests found before any of them could reach the real
   rollout. **Real-rolled-out 2026-09-29**: 70,145/70,145 extractions (186 explicitly truncated),
   358,031 entity mentions, 254,727 relation candidates; six more real defects found and fixed
   during the rollout (PRs #78-#80). The person layer stays empty until the identity bootstrap
   (people + aliases), which is the next backend step. See
   [`PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md`](PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md) and
   `PROJECT_STATE.md`'s "Phase 10: real rollout".

Deep understanding begins in this layer. Import adapters know what provider objects say; derived
claims express what multiple observations may mean together.

Phases 0–5 of [`DERIVED_MEANING_ARCHITECTURE.md`](DERIVED_MEANING_ARCHITECTURE.md) (activity
coverage, as-of/contract derivation, identity's COMPUTED rules tier, the life-ledger metrics/
compare layer, signals + episodes, and the claims ledger above) are all live against the real
canonical database as of 2026-09-24 — see `PROJECT_STATE.md` for full rollout evidence on each.
That architecture's own phase numbering does not map one-to-one onto items 3–6 above. Items 5
(semantic retrieval), 6 (OCR/media enrichment), 7 (topic clustering), and item 3's
`relationship_state_shift` claim template are all now real-rolled-out against the canonical
database — see `PROJECT_STATE.md`. Item 8 (the life graph) is implemented and tested but not yet
real-rolled-out — it needs one explicitly-authorized rented-GPU pass; see the milestone table
below.

### Phase 5: claims + evidence challenge — landed and rolled out 2026-09-24

[`PHASE5_IMPLEMENTATION_PLAN.md`](PHASE5_IMPLEMENTATION_PLAN.md) scoped `DERIVED_MEANING_ARCHITECTURE.md`
section 15's phase 5 against what already existed after Phases 0–4: a `know.claim` schema
(evidence cited inline as JSONB, after a same-day correction to the plan's original separate
`know.claim_evidence` table — this project's deriver framework can't populate a child table from
the same `compute()` call) wrapping the real `signal.anomaly` and `episode.episode` rows into
inspectable claims (two claim templates, `communication_change` and `episode_evidence`), 4 of the
architecture's 9 confidence dimensions (the ones this corpus can actually compute today), and
`claims-list`/`claims-why` CLI verbs realizing section 9's evidence inspector without a query
router or LLM layer this project hasn't built yet. Building the runtime also surfaced and fixed a
real pre-existing scheduler ordering defect (`derive/worker.py`). Rolled out against the real
canonical database: 1,181 claims, `claims-why` independently re-fetched every cited evidence row
with an exact match. See `PROJECT_STATE.md`'s "Phase 5 claims ledger: rollout" for full evidence,
including a real, un-fixed weakness the rollout found in the `coverage` dimension's aggregation.

## 4. Build user and agent experiences

See [`COCKPIT_VISION.md`](COCKPIT_VISION.md) for the full end-state product vision (an adaptive,
question-driven visual cockpit) this section works toward. It is north-star context, not a current
spec.

**UI v1 shipped 2026-09-29** (`personal-data-lab serve`): a deliberately plain, functional local
app — Ask with a guided planner and an optional OpenAI-compatible agent, relationships over time,
topics, claims with refetched evidence, full-text and semantic search, people and identity review
(including bulk mention confirmation), saved investigations with refresh-and-diff, and an evidence
drawer that opens every finding down to the original message and thread. It covers the evidence-
citing question answering and the people/topic/conversation views below at a basic level; the
visual cockpit, timeline canvas, geography, and MCP remain future work.

- unified personal timeline;
- people, place, project, topic, conversation, and document views;
- geographic and communication exploration;
- a question-driven visual canvas that can fluidly transform among timelines, relationship
  topology, geography, topic structure, and evidence detail as the user asks follow-up questions;
- DuckDB/Parquet analytical projections without creating a second source of truth;
- evidence-citing question answering;
- read-only, permission-scoped MCP access;
- agent workflows only after normal queries are fast, provenance-aware, and access-controlled.

The visual layer may use ambitious motion, dimensional transitions, and novel interactive
encodings, but it must remain an evidence view rather than a second analytical truth. Every visual
mark must resolve to canonical objects and immutable observations; animation must preserve mental
map and selection state; large corpora need performance budgets and progressive detail; and every
experience needs reduced-motion, keyboard, screen-reader, and inspectable static alternatives.

## Permanent operational track

Every phase also advances encrypted backup/restore, integrity audits, local-only secrets,
incremental import scheduling, restart monitoring, sensitive-field controls, performance budgets,
derived-data invalidation, and exportability. The raw corpus, canonical history, and evidence
chain must never depend on an embedding model, UI, or agent remaining available.

## Milestone record and ordered next work

| Status | Outcome | Required completion gate |
|---|---|---|
| Completed PR12 | Integrate PR7–PR11 onto `main`; replace the stale roadmap | Full cumulative CI on the real default-branch base |
| Completed PR13 | Google production acceptance and corrected differential reimport | Complete accounting, safe resume, clean affected-member replay, no unintended second-run versions |
| Completed PR14 | Official Apple privacy-export ingestion and review fixes | Recursive accounting, review defects fixed, synthetic regression gate passed |
| Completed PR15 | Separate iMessages device-export importer | Complete accounting, safe resume, exact identical replay, no unintended versions |
| Completed PR16 | Official Apple parser-v3 real-corpus operational acceptance | Fresh import, safe resume, empty-dataset repair, semantic reconciliation, exact replay |
| Completed PR17 | Official OpenAI production ingestion and real-corpus acceptance | Manifest accounting, safe resume, complete graph/assets, exact replay |
| Completed PR19 | Anthropic production ingestion and retained-corpus acceptance | Privacy-safe inventory, complete disposition, bounded restart, graph/assets, controlled resume, exact replay |
| Completed PR23 | Apple production readiness | Lossless representation contract, separate interpretation depth, full-corpus import and exact replay, exit 0 |
| Completed 2026-09-19 | Canonical database rebuild: all five sources imported fresh from raw exports into one dedicated-volume database | Apple resumed to its accepted 897-run baseline after an unrelated interruption; Google ran one fresh complete pass, 25,427/25,427 members, zero failed/unknown, exit 0 |
| Completed 2026-09-19 | Canonical-database idempotency verification | Apple and Google each re-run a second time against the combined canonical database: all members short-circuited, zero drift, exit 0 for both |
| Completed 2026-09-20 | Official Facebook export ingestion | New source family: inventory, product registry, adapters, orchestrator, and tests for the retained "Download Your Information" export; 2,673/2,673 members, exit 0, `production_ready: true`, exact replay verified against an isolated database |
| Completed 2026-09-20 | Import Facebook into the canonical database | 2,673/2,673 members, exit 0, zero conflicts, identical to the isolated acceptance run; a second identical pass short-circuited all members with zero drift |
| Completed 2026-09-20 | Remaining Google semantic gaps | Nest's 31 doubly-escaped rows recovered to real structured content (`partially_structured` -> `fully_structured`); Wallet's `asset_indexed` disposition confirmed already correct, not a gap |
| Completed 2026-09-20 | Re-run Google against the canonical database | All 25,427 members, exit 0, zero failures/unknowns/conflicts; `nest_event` rows went from 35 (31 opaque) to 66 (31 opaque preserved + 31 newly recovered); not re-verified for exact replay (deferred by explicit instruction — low priority given the fix's narrow scope and existing test coverage) |
| Completed 2026-09-20 | Unified canonical full-text query | Schema version 8 (`search_vector`/GIN/trigger on all four canonical tables), ranked `search` CLI command spanning all six sources, 293 tests passing, zero importer changes required |
| Completed 2026-09-20 | Apply schema version 8 to the canonical database | `personal-data-lab migrate` backfilled `search_vector` for ~1.19M existing rows (2,133 conversations, 524,953 events, 642,998 records, 22,525 assets); verified with real queries (Gmail "meeting", cross-source "photo", asset-filename "img") |
| Completed 2026-09-20 | Reversible identity resolution | Schema version 9 (`identity_links` table, `records.actor_person_id`), five `identity-*` CLI commands, 311 tests passing, zero importer changes required, evidence-checked and fully reversible merge/split |
| Completed 2026-09-20 | Apply schema version 9 to the canonical database | `personal-data-lab migrate` added the empty `identity_links` table and `records.actor_person_id` column (additive, no backfill); verified with a real merge linking "Ryan Hallgarth" (facebook, 16,624 events) and "Ryan Hallgarth <rhallgarth88@gmail.com>" (google, 596 events) to one person, a real split reverting the Gmail link and nulling `actor_person_id`, and a re-merge inserting a new `identity_links` row while the reverted one stayed in place |
| Completed 2026-09-22 | `DERIVED_MEANING_ARCHITECTURE.md` Phase 0: derivation foundation | Schema version 10 (`derive`/`declare`/`policy`/`measure` schemas), a generic versioned deriver/change-feed/worker runtime, applied to and computed against the real canonical database |
| Completed 2026-09-22 | Phase 1: coverage and as-of contracts | Schema version 11, day-grain source coverage and point-in-time reconstruction over the real canonical database |
| Completed 2026-09-22 | Phase 2: identity COMPUTED-tier rules and assertion log | Schema version 12 (`identity.assertion`/`identity.not_same_as`, `identity_links.tier`/`resolution_version`), an append-only log underneath the existing manual `identity_links`; real rollout found zero real auto-linkable pairs (an honest result, not a bug) and surfaced a real cross-source name-candidate queue for human review |
| Completed 2026-09-22 | Phase 3: life ledger metrics and `compare` | Schema version 13 (`derive.metric_def`, `measure.metric_daily`), 20 per-source/composite communication metrics; real values cross-checked against independent raw-event counts, 37/37 matched exactly |
| Completed 2026-09-22 | Phase 4: signals and episodes | Schema version 14 (`signal`/`episode` schemas), baselines/change-points/anomalies over Phase 3's metrics, per-correspondent interaction dynamics/states, and cross-source `detected` episode candidates; top anomaly independently cross-checked against raw events, 106/106 matched exactly; 9 real episode candidates spanning 2016–2026 — see `PROJECT_STATE.md` for full rollout evidence |
| Completed 2026-09-24 | Fix the recurring new-deriver onboarding gap | `sync()` now backfills any deriver seeing its first-ever `derive.deriver_version` row from its upstream's (or, for a root, `declare.source_contract`'s) existing partition space, permanently replacing the one-off script each of Phases 1/3/4 needed on rollout; 4 new tests, 361 total passing, ruff clean — see `PROJECT_STATE.md`'s "New-deriver onboarding fix" |
| Completed 2026-09-24 | Phase 2/4 human review session | Identity: both cross-source name-candidates confirmed and merged, queue now empty. Episodes: `episode-confirm`/`episode-reject` built (didn't exist), 8/9 detected episodes confirmed with real evidence-backed labels, 1 left honestly unresolved, 1 additional declared episode added for a death the automatic detector missed entirely — see `PROJECT_STATE.md`'s "Done 2026-09-24" notes (private details intentionally not in this repo) |
| Completed 2026-09-24 | Phase 5: claims + evidence challenge | Schema (`know.claim`, corrected same-day from a separate child table to inline JSONB evidence), `know_claim_evaluate` runtime, `claims-list`/`claims-why` CLI, and a real scheduler ordering defect found and fixed (`derive/worker.py`); rolled out against the real canonical database, 1,181 real claims, every cited evidence row independently re-fetched and matched exactly by `claims-why` — see `PROJECT_STATE.md`'s "Phase 5 claims ledger: rollout" |
| Completed 2026-09-24 | Phase 6: semantic layer (text units + local embeddings) | Schema (`semantic.text_unit`/`semantic.embedding_bge_small_en_v15`, pgvector), `semantic_text_unit`/`semantic_embedding_bge_small` derivers (`fastembed`/`BAAI/bge-small-en-v1.5`, fully local), `semantic-search` CLI; found and fixed a second scheduler gap (HALO-class root dirtying) and a real concurrency race (operational mistake, not a code defect, self-diagnosed and corrected with zero data loss); rolled out against the real canonical database, 70,145 real text units, 70,145 real embeddings (exact 1:1, independently verified), `semantic-search` correctly surfacing real messages from the same thread the 2026-09-24 episode review already confirmed — see `PROJECT_STATE.md`'s "Phase 6 semantic layer: rollout" |
| Completed 2026-09-26 | Item 6: media transcription (OCR/transcription) | Schema (`media.transcript`) and `media_transcribe_whisper` shipped and fully rolled out against the real canonical database — 487 real audio files (22 apple + 6 facebook + 459 google), all three undated partitions `clean`. Local CPU transcription was abandoned after 42+ hours with no completion in sight (extreme file-size long-tail); finished instead on rented GPU (RunPod H200 then RTX 4090, `faster-whisper` `BatchedInferencePipeline`, ~$9 total, ~2h20m combined) — see `PROJECT_STATE.md`'s "Media transcription" sections for the full story, including two real code bugs fixed live (Facebook locator dispatch, Apple/Facebook temp-file suffix parsing), a post-rollout Whisper-hallucination cleanup (51 rows fixed, not yet automated into the deriver), a real Apple-export-scope finding (only 22 audio files exist in the official export, third-party-app audio never included, deferred to a future "v1.1" iMazing-backup source), and an unexpected discovery of two AI-generated podcast-style audio files in the corpus |
| Completed 2026-09-27 | Semantic annotation/topic-labeling (Phase 7) | `PHASE7_SEMANTIC_ANNOTATION_IMPLEMENTATION_PLAN.md`, migration 019 (`signal.annotation`), `signal_annotation_topic` — the codebase's first real `SEMANTIC`-class deriver, grammar-constrained (GBNF/`llama.cpp`) closed-taxonomy topic labels; real production-model rollout completed against a rented Secure Cloud GPU (Phi-3.5-mini-instruct, single-stream after two multi-process parallelism attempts both failed and were root-caused): 70,145/70,145 real annotations, exact 1:1, 933/933 partitions clean, 0 failed — see `PROJECT_STATE.md`'s "Phase 7: real rollout" section for full evidence including three real infrastructure defects found and fixed |
| Completed 2026-09-26 | Phase 8: documents into the semantic layer | `PHASE8_DOCUMENT_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md`, migration 021 (`semantic.text_unit_document`/`semantic.embedding_bge_small_en_v15_document`), `semantic_text_unit_document`/`semantic_embedding_document_bge_small`, `semantic-search --include-documents`; found while investigating transcription redundancy that Google Recorder audio already has real transcripts sitting unused in `records`; two real bugs found and fixed before merging (a `NOT NULL` timestamp constraint that would have forced fabricating dates for the ~all-undated real population, and a `%` character breaking `psycopg`'s SQL parameter substitution); real rollout completed against canonical: 597/597 real text units + embeddings, exact 1:1 — see `PROJECT_STATE.md`'s "Phase 8" section |
| Completed 2026-09-25 | Item 3: further claim-template growth (`relationship_state_shift`) | Migration 020, wraps Phase 4's already-computed `signal.interaction_state` segments the same way `episode_evidence` wraps episode detection; grounded against the real canonical database (77 states, 69 correspondents, 8 real transitions); real rollout against canonical completed — 8 real claims — see `PROJECT_STATE.md`'s "Claim-template growth: relationship_state_shift" |
| Completed 2026-09-25 | Operational track: encrypted backup/restore | `personal-data-lab backup`/`restore`, `pg_dump`/`pg_restore` via the project's own Postgres container, passphrase-derived streaming AES-256-CTR + HMAC-SHA256 encrypt-then-MAC (`cryptography`, chunked, not single-shot), canonical-database restore guarded behind `force=True`; real round-trip test plus wrong-passphrase/missing-passphrase coverage; a real `MemoryError` from an initial `Fernet`-based (single-shot, whole-payload-in-memory) implementation was found running a real backup against the 13GB+ canonical database and fixed by switching to streaming — see `PROJECT_STATE.md`'s "Operational track: encrypted backup/restore" |
| Completed 2026-09-26 | Disposability verification (invariant 20) -- two real bugs found and fixed | Real backup restored into a scratch database, checksummed, fully rebuilt (`derive-rebuild` + `derive-run --global`), and re-checksummed; found and fixed a tied-timestamp ordering bug in `semantic_text_unit` (12/70,145 real units affected) and a duplicate-candidate bug in `episode_detect_candidates` (would have duplicated all 8 real confirmed episodes on the next ordinary re-run, not just a rebuild); both fixed with regression tests, neither required real-canonical cleanup — see `PROJECT_STATE.md`'s "Disposability verification (invariant 20)" |
| Completed 2026-09-28 | Item 7: unsupervised topic clustering | HDBSCAN over Phase 6's real embeddings, TF-IDF extractive labels, GLOBAL/STATISTICAL deriver, versioned + disposable per invariant 20; parameters grounded against a real six-combination timed benchmark on the full 70,145-vector population (25/5 chosen at a real noise-vs-fragmentation elbow); a real production bug found and fixed (the deriver's own `_cluster()` was missing the `n_jobs` parallelism the benchmark required, causing a real rollout attempt to sit idle for 10+ minutes before being caught); real rollout completed: 397 groups (396 clusters + noise), exact 1:1 row count — see `PHASE9_TOPIC_CLUSTERING_IMPLEMENTATION_PLAN.md` and `PROJECT_STATE.md`'s "Phase 9" section |
| Completed 2026-09-29 | Item 8: the life graph | Migrations 023-024, 5 deriver modules, 15 CLI verbs, exit tests all passing. Real rollout on a rented RTX PRO 6000 Blackwell (llama.cpp server, 24 slots, `--no-cache-prompt`): 70,145/70,145 extractions (69,959 complete, 186 explicitly truncated), 358,031 entity mentions, 254,727 relation candidates, 32,495 time resolutions, every partition clean; ~20h fetch (generation-bound, not the benchmarked 7.4h, after a prompt-cache-thrashing fix), $61.37 total GPU spend; six real defects fixed during the rollout (truncated envelopes blocking 41 percent of partitions, `model_ref` gaps, `promote()` not seeding partitions, a masked grammar hole, a DB error stopping the whole worker run, O(n²) resolvers) — see `PROJECT_STATE.md`'s "Phase 10: real rollout". Person layer empty until the identity bootstrap |
| Next (backend) | Identity bootstrap | Ranked provider-identity review queue (165 identities with 100+ events), alias support in `entity_resolve_mentions`, then re-run the global resolvers; the review surface itself is UI work |
| Completed 2026-09-29 | UI v1 (functional local app) | FastAPI product API + SolidJS app served by `personal-data-lab serve`; typed read-only tool registry, guided planner plus optional OpenAI-compatible agent with evidence validation and a message-text privacy switch, opaque evidence handles, saved investigations with drift diffs, loopback-only with Host/session/CSRF guards; real-data acceptance run — see `PROJECT_STATE.md`'s "UI v1" section |
| Final product track | Timeline canvas, geography, cockpit, read-only MCP, and agents | Fast normal queries, permission boundaries, and evidence-citing answers |

