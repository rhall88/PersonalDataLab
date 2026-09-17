# iMessages device-export coverage

## Retained corpus

| Surface | Observed | Representation |
|---|---:|---|
| iMazing CSV files | 2 | immutable source files plus restartable parsed chunks |
| physical bytes | 11,913,887 | fully inventoried, source remains read-only |
| message rows | 67,917 | normalized message events |
| chat sessions | 103 | normalized conversations |
| participant identities | 70 | sender identity records |
| reaction entries | 3,498 | reaction events and target relationships |
| edited messages | 364 | edit events and current message versions |
| deleted messages | 10 | deletion events plus provider-deleted lifecycle state |
| reply references | 708 | resolved relationships or explicit ambiguous references |
| attachment references | 3,136 | source-backed references; retained export has no media bytes |

The two retained files have no overlapping chat-session labels. Six exact duplicate rows are
preserved as distinct observations through deterministic occurrence identity. The CSVs contain
no embedded device identifier, export timestamp, timezone, or completeness manifest; none is
invented.

## Supported source variants

- exact observed 17-column iMazing Messages schema;
- UTF-8 and UTF-8 with BOM;
- comma, semicolon, or tab delimiter;
- multiline quoted text and reaction fields;
- iMessage, SMS, blank service values, and preserved unknown service text;
- Incoming, Outgoing, and Notification rows;
- sent, delivered, read, not-delivered, and recently deleted status values;
- reference-only attachments and optional uniquely resolvable local attachment files.

RSMF, raw iOS `sms.db`, PDF, Excel, and plain-text exports are not present in the retained corpus
and are not claimed by this importer. They require their own evidence-shaped adapters if later
provided.
