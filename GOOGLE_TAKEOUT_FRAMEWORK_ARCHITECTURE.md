# Google Takeout framework architecture (PR5–PR10)

The framework keeps Takeout container mechanics, product semantics, and canonical persistence
separate:

1. `google_takeout.inventory` discovers ZIP members from central-directory metadata or files in
   an extracted tree. It classifies every member without extracting archives or reading obvious
   payloads.
2. `google_takeout.products` is the permanent, machine-readable 68-product contract. Display
   names are aliases; stable canonical keys are used by code and persistence.
3. `google_takeout.adapters` defines the adapter protocol and deterministic registry. The PR4
   My Activity parser is wrapped as the first fully structured adapter. Known simple structured
   files can use a conservative, partially structured fallback; all other members remain
   explicitly raw-preserved.
4. `google_takeout.orchestrator` routes one member at a time, reports product/member coverage,
   and never treats an unmatched or unsupported member as imported.
5. The existing cumulative database layer gains durable `records` and external-reference
   `assets`, each with distinct versions and exact observations. Reconciliation uses the same
   authority, lifecycle, fingerprint, conflict, and no-absence-deletion rules as conversations
   and events.
6. PR6 adds `google_takeout.semantic` for product-owned geographic, Calendar, Contacts, and Tasks
   semantics. Provider-native fields remain under `metadata.google`; normalized annotations live
   under `metadata._pdl`.
7. Migration 007 adds durable `relationships`, immutable relationship versions, exact source-file
   memberships, and per-run observations. Relationship endpoints use stable provider namespaces
   and IDs, so evidence can be retained even when an endpoint arrives in a different member.
8. PR7 adds `google_takeout.communications`. Gmail MBOX input is SHA-256 hashed in a first pass,
   parsed incrementally in a second pass, and yielded as deterministic bounded chunks. Chat,
   Voice, Meet, and Groups retain product-native fields while promoting messages, calls,
   conversations, memberships, and external assets into their semantic objects.
9. PR8 adds `google_takeout.rich_assets`. Photos sidecars and albums, Drive documents, and
   Recorder transcripts become semantic records linked to path-stable external assets. Plain
   text, structured files, and Office Open XML receive bounded text extraction. All other media,
   archives, and documents are streamed only to compute external-asset identity and never stored
   as database binary content.
10. PR9 adds `google_takeout.ai_knowledge`. Provider IDs anchor Flow project/generated-media
    relationships; path-stable notebook identities connect NotebookLM metadata, source documents,
    audio, video, and other assets across split Takeout members. Keep microsecond timestamps and
    Journal seconds/nanoseconds are normalized without dropping provider-native values.
11. PR10 adds `google_takeout.account_commerce`. It preserves typed account, subscription,
    payment, purchase, device, home, Play Store, and provider-event representations. Nest's
    provider-specific CSV-quoted JSONL is decoded when valid; malformed rows remain explicit
    opaque events with warnings rather than being dropped or repaired speculatively.

One source member is the parsing and transaction boundary. ZIP streams are opened only for the
selected adapter; no archive is extracted. Adding a new Google product normally requires one
registry entry, one registered adapter, and fixtures/tests—not an orchestrator or schema redesign.

Hashing is deliberately staged. Structured members are SHA-256 hashed from the exact bytes read
by their parser. Inventory-only binaries are not force-read: their locator, size, compression
metadata, and ZIP CRC remain inventory evidence, but CRC is never represented as cryptographic
identity. A future product adapter that promotes a binary to a durable asset must compute SHA-256
when content identity/deduplication requires it.

The generic fallback is capped at 64 MiB per member because it performs whole-member JSON, CSV,
or XML parsing. Larger structured members are explicitly raw-preserved until a product-specific
incremental adapter exists. My Activity retains PR4's streaming decoder. The orchestrator also
accepts chunk-yielding adapters: source member and byte totals are counted once, while every
chunk receives an independent transaction boundary and restart-safe run category.

Semantic adapter upgrades intentionally change the parser version and representation namespace.
That makes source bytes previously handled by `generic_structured` eligible for semantic
reprocessing while preserving the old generic records and observations as immutable history.
