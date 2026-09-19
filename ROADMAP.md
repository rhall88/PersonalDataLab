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

Add later providers only when source evidence exists; every provider starts with inventory and
explicit disposition rather than optimistic format assumptions.

## 3. Build query and understanding layers

1. **Unified full-text query:** searchable conversations, events, records, document text, and
   asset metadata with direct provenance inspection.
2. **Identity resolution:** reversible evidence-backed links among provider identities, people,
   accounts, and devices, with manual merge and split controls.
3. **Derived entities and claims:** versioned, confidence-scored assertions about people,
   organizations, places, projects, topics, documents, and relationships. Every assertion must
   cite immutable observations and remain removable without changing source truth.
4. **Event correlation:** link observations that may describe the same real-world occurrence
   without deleting or merging the original provider surfaces.
5. **Semantic retrieval:** deterministic chunking and locally controlled embeddings as
   disposable derived data, never as canonical evidence.
6. **Optional media enrichment:** OCR, transcription, and document/image analysis only in a
   separately versioned derived layer. Core ingestion never fabricates these results.

Deep understanding begins in this layer. Import adapters know what provider objects say; derived
claims express what multiple observations may mean together.

## 4. Build user and agent experiences

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
| Open PR19 | Anthropic production ingestion and retained-corpus acceptance | Privacy-safe inventory, complete disposition, bounded restart, graph/assets, controlled resume, exact replay |
| Current stacked PR | Apple production readiness | Lossless representation contract, separate interpretation depth, full-corpus import and exact replay, exit 0 |
| Next stacked PR | Remaining Google semantic gaps | Complete or explicitly bound retained Nest and asset-indexed surfaces without regressing accepted accounting |
| Then | Unified canonical full-text query | Fast source-spanning queries with direct observation/version provenance and regression coverage |
| Then | Reversible identity resolution, claims, correlation, and semantic retrieval | Every derived object cites evidence, is versioned, and can be removed without changing source truth |
| Final product track | Timeline, exploration, analytics, read-only MCP, and agents | Fast normal queries, permission boundaries, and evidence-citing answers |

