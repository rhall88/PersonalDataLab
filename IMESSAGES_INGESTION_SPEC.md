# iMessages device-export ingestion specification

## Scope and provenance boundary

This importer accepts independently acquired iMazing Messages CSV exports. It is deliberately a
different source family from the official Apple privacy export:

- normalized source: `imessages`;
- logical provider: Apple;
- acquisition source: iPhone device or backup;
- evidenced acquisition method: iMazing CSV export;
- snapshot kind: partial, because the CSV contains no device-wide completeness manifest.

The importer never infers a device serial, backup identity, export time, or timezone from a
filename. An optional operator-supplied device label is retained only in import-run provenance.
The IANA timezone is mandatory because retained CSV timestamps are timezone-naive.

## Inventory and privacy

`inspect-imessages` recursively accounts for every source file and byte. CSV classification
requires the exact observed 17-column iMazing Messages schema. Comma, semicolon, and tab
delimiters are supported because iMazing documents all three. Recognized external attachment
files are accounted separately; unknown files make the inventory non-production-ready.

Default JSON and all progress output use opaque member IDs. Paths and filenames are available
only through the explicit `--include-paths` inspection option. Errors identify the structural
failure without echoing message text, contact details, filenames, or raw rows.

## Identity and reconciliation

The CSV has no provider message GUID. Message identity therefore uses the complete stable tuple
of chat session, provider timestamp text, sender identity, direction, service, and deterministic
same-tuple occurrence. The occurrence prevents identical same-second rows from collapsing while
remaining stable across chunk sizes and exact reimports. Mutable status, body, reactions, edit
state, and deletion state are excluded from identity and retained in versioned content.

Conversation identity is a digest of the exported chat-session identity. Participant identity
prefers `Sender ID` and falls back to the displayed sender name only when the ID is absent.
Provider lifecycle timestamps establish reconciliation authority. A changed payload without
newer authority remains a conflict rather than being selected by local ingestion order.

## Semantic normalization

The importer produces:

- conversations with start/end bounds and participant membership;
- incoming, outgoing, and notification message events;
- participant records and message-sender relationships;
- reply relationships resolved by conversation, timestamp, actor, and exported preview;
- explicit reply-reference records when duplicate source evidence is genuinely ambiguous;
- reaction events and reaction-to-message relationships;
- edit and deletion events linked to their message;
- provider-deleted message lifecycle state without discarding message content or active history;
- attachment assets and message-to-attachment relationships.

When attachment bytes are absent, the asset remains an honest reference-only object with null
hash and zero known size. The importer does not claim that referenced media was retained.

## Operational contract

CSV rows are processed in bounded 2,000-row database transactions. A lightweight profile pass
builds only conversation, participant, and referenced-reply indexes; normalized rows are not
held for the full corpus. Every completed chunk is restartable. The run is production-ready only
when every file and message is accounted for, unknowns/failures/conflicts/warnings are zero, and
every reply is either resolved or preserved as an explicit unresolved source reference.

Exit codes are `0` for complete success, `2` for reconciliation conflict, `3` for incomplete
accounting or warnings, `4` for a failed CSV, and `5` for an unknown source file.
