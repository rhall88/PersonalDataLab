# PersonalDataLab roadmap after OpenAI production acceptance

PersonalDataLab develops in four layers. Each layer depends on the evidence guarantees beneath
it: acquire and preserve first, normalize second, derive understanding third, and expose that
understanding to products and agents last.

## 1. Google operational acceptance — completed in PR13

The PR5–PR11 implementation covers the 68-product contract and routes every retained Takeout
member. Production acceptance remains a separate gate:

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

## 2. Add source families without mixing provenance

### Official Apple privacy export — parser-v3 operational acceptance completed in PR16

The retained export now has recursive inventory, a 32-surface registry, complete physical versus
logical byte accounting, privacy-safe progress, structured and native-format adapters, streamed
external assets, restart-safe transactions, and coverage reporting. A complete real-data
PostgreSQL import, controlled resume, and exact unchanged replay passed for parser v2. Review
found defects outside exact-replay coverage; parser v3 fixed them. PR16 completed a fresh
real-corpus import, controlled resume, semantic reconciliation, a real empty-TestFlight repair,
and exact unchanged replay. Generic structured coverage remains explicitly partial, so exit 3 is
the honest semantic-coverage result rather than a persistence failure. Do not infer support for
products or formats absent from the retained export. See
[`PR16_APPLE_V3_PRODUCTION_ACCEPTANCE_REPORT.md`](PR16_APPLE_V3_PRODUCTION_ACCEPTANCE_REPORT.md).

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

### Anthropic and later providers

Bring Anthropic to the same inventory, asset, relationship, restart, and real-export acceptance
standard. Add later providers only when source evidence exists; every provider starts with
inventory and explicit disposition rather than optimistic format assumptions.

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
- DuckDB/Parquet analytical projections without creating a second source of truth;
- evidence-citing question answering;
- read-only, permission-scoped MCP access;
- agent workflows only after normal queries are fast, provenance-aware, and access-controlled.

## Permanent operational track

Every phase also advances encrypted backup/restore, integrity audits, local-only secrets,
incremental import scheduling, restart monitoring, sensitive-field controls, performance budgets,
derived-data invalidation, and exportability. The raw corpus, canonical history, and evidence
chain must never depend on an embedding model, UI, or agent remaining available.

## Ordered next PRs

| PR | Outcome | Required completion gate |
|---|---|---|
| PR12 | Integrate PR7–PR11 onto `main`; replace the stale roadmap | Full cumulative CI on the real default-branch base |
| PR13 | Complete Google production acceptance and corrected differential reimport | Complete accounting, safe resume, clean affected-member replay, no unintended second-run versions |
| PR14 | Official Apple privacy-export ingestion review fixes | Review defects fixed and synthetic regression gate passed |
| PR15 | Separate iMessages device-export importer | Complete accounting, safe resume, exact identical replay, no unintended versions |
| PR16 | Official Apple parser-v3 real-corpus acceptance | Fresh import, safe resume, empty-dataset repair, semantic reconciliation, exact replay |
| PR17 | Official OpenAI production ingestion and real-corpus acceptance | Manifest accounting, safe resume, complete graph/assets, exact replay |
| Following PRs | Anthropic parity, unified search, identities, claims, semantic retrieval | Each derived object cites evidence and remains reversible |
| Final product track | Timeline, exploration, analytics, MCP, and agents | Fast normal queries, permission boundaries, and evidence-citing answers |

