# PersonalDataLab roadmap after Google PR11

PersonalDataLab develops in four layers. Each layer depends on the evidence guarantees beneath
it: acquire and preserve first, normalize second, derive understanding third, and expose that
understanding to products and agents last.

## 1. Finish Google operational acceptance

The PR5–PR11 implementation covers the 68-product contract and routes every retained Takeout
member. Production acceptance remains a separate gate:

1. import the complete retained Takeout into a fresh production-shaped local database;
2. reconcile all discovered members, bytes, normalized objects, assets, and relationships;
3. interrupt and resume a controlled run without losing completed member transactions;
4. repeat the complete unchanged import;
5. prove that the second run creates no unintended canonical objects or versions;
6. record bounded-memory, elapsed-time, database-size, backup, and restore evidence;
7. publish only sanitized counts and operational results.

Google is production-accepted only after both complete runs succeed. Routing audits and
representative binary canaries do not substitute for this gate.

## 2. Add source families without mixing provenance

### Official Apple privacy export

Inventory the actual Apple archives before choosing semantic slices. Build Apple-specific
registry, accounting, adapter routing, coverage, restart, and provenance contracts. Then divide
the observed formats into coherent structured-data and rich-asset PRs. Do not infer support for
products or formats that are absent from the retained export.

### iMessages device export

Treat independently acquired iPhone/iMazing/RSMF/CSV messages as a separate source family, even
though the logical ecosystem is Apple. Preserve acquisition method and device provenance while
normalizing conversations, participants, messages, replies, reactions, edits, deletions, and
external attachments.

### Existing and later providers

Bring OpenAI and Anthropic to the same coverage, asset, relationship, and real-export acceptance
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
| PR13 | Complete Google production acceptance and identical reimport | Full member/object reconciliation, safe resume, no unintended second-run versions |
| PR14 | Apple privacy-export inventory and framework | 100% archive/member/byte accounting with zero silent skips |
| PR15+ | Evidence-shaped Apple semantic and asset adapters | Per-family real canaries, provenance, idempotency, and coverage |
| Following PR | Separate iMessages device-export importer | Conversation/message/attachment reconciliation and identical reimport |
| Following PRs | Provider parity, unified search, identities, claims, semantic retrieval | Each derived object cites evidence and remains reversible |
| Final product track | Timeline, exploration, analytics, MCP, and agents | Fast normal queries, permission boundaries, and evidence-citing answers |
