# Google Takeout framework architecture (PR5)

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
incremental adapter exists. My Activity retains PR4's streaming decoder and existing one-member
normalization boundary.
