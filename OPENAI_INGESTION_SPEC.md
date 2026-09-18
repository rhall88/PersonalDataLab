# Official OpenAI data-export ingestion specification

## Scope and provenance boundary

This importer accepts an untouched official OpenAI export ZIP or an extracted copy containing
`export_manifest.json`. Its normalized source is `openai`, and its snapshot scope is
`openai_account_export`. A standalone legacy `conversations.json` remains supported by the older
single-file importer but does not satisfy this production contract because it has no manifest or
whole-export asset accounting.

## Source safety and inventory

- The ZIP is opened read-only and is never extracted over the retained source.
- Archive paths reject absolute paths and parent traversal; duplicate or encrypted ZIP members
  fail inspection.
- The manifest must identify itself, list every physical member exactly once, declare consistent
  sharding, and match the retained members and byte sizes exactly.
- Every physical member belongs to exactly one logical file and one disposition: manifest,
  conversation shard, structured JSON, binary asset, rendered HTML, or unknown.
- Default inventory and progress output use opaque member IDs. Paths require the explicit
  `--include-paths` option.
- Asset bytes remain external. PostgreSQL stores hashes, sizes, locators, metadata, and
  provenance, not the binary or rendered payload.

Physical bytes count all retained members, including the manifest and physical conversation
shards. Logical bytes follow the manifest's logical-file grouping and therefore exclude the
manifest itself. Complete accounting requires exact manifest membership, exact sizes, and one
logical owner for every listed member.

## Semantic normalization

Conversation shards are processed in bounded groups of 25 conversations. Each conversation
retains its provider metadata and mapping graph. Every message-bearing mapping node becomes one
event whose identity is the conversation ID plus mapping-node ID. The provider message ID remains
metadata rather than canonical identity because a retained export can reuse it in more than one
conversation. Parent mapping nodes become `message_has_parent` relationships.

Text is derived from provider text-bearing content fields without discarding the complete content
and message metadata. A literal NUL is represented as the visible string `\u0000`, preserving its
position because PostgreSQL text and JSONB cannot store U+0000; the untouched source remains the
lexical authority.

Every `sediment://` pointer in message content or metadata becomes a `message_has_asset`
relationship. Present `.dat` members become external assets with streamed SHA-256 and byte count.
References omitted from the export become explicit zero-byte, null-hash unavailable assets. The
importer never claims missing bytes were retained.

All other JSON members, including the manifest, become full-fidelity versioned records.
`library_files.json` records link to matching exported assets. HTML and bundle members are
external assets. Unknown files prevent the production gate.

## Restart, reconciliation, and exit codes

Every asset, structured member, and bounded conversation chunk commits independently through the
cumulative lossless reconciliation layer. A process interruption retains completed transactions;
rerunning the same source/parser/category reuses them. Absence never creates deletion, and
conflicting representations remain versioned rather than being resolved by ingestion order.

Exit codes are `0` for complete success, `2` for conflicts, `3` for warnings or incomplete
accounting, `4` for failed accounted members, and `5` for unknown members. Precedence is failure,
unknown, conflict, warning/incomplete accounting, success.

## Production acceptance gate

Production-ready means all of the following are true for the retained corpus:

1. manifest membership, physical/logical members, and byte counts reconcile;
2. every accounted member produces one or more durable import runs;
3. unknown members, failures, conflicts, and parse warnings are zero;
4. every conversation, message, structured record, asset, and relationship is retained;
5. missing provider assets are explicit rather than discarded;
6. a controlled interruption resumes without losing completed work;
7. a complete unchanged rerun short-circuits every physical member and creates no new versions;
8. the complete PostgreSQL suite, Ruff, formatting, and Python compilation pass;
9. no source payload, source path, generated inventory, runtime log, or database is committed.
