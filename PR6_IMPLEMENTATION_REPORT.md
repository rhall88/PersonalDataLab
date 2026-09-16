# PR6 implementation report: high-value Google personal timeline

## Scope

PR6 implements semantic adapters for the eight contracted products:

1. Timeline
2. Maps
3. Maps (your places)
4. My Maps
5. Saved
6. Calendar
7. Contacts
8. Tasks

Apple and independently acquired iMessages are not part of this pull request.

## Architecture delivered

- Migration 007 adds canonical relationships, immutable relationship versions, source-file
  membership, and exact run/file/version observations.
- `NormalizedRelationship` gives every edge a deterministic provider namespace and identity,
  typed endpoints, lifecycle, authority, and lossless metadata.
- Semantic parser versions differ from PR5's generic fallback, so unchanged source bytes are
  intentionally eligible for semantic reprocessing. Existing generic records remain immutable.
- Provider-native fields remain under `metadata.google`; adapter annotations remain under
  `metadata._pdl`.
- Calendar recurrence is stored as a definition and is never expanded without a bounded query
  window.
- Geographic adapters standardize only provider-supplied coordinates. They do not geocode,
  infer visits, or merge places by name.

## Synthetic verification

The PR6 tests cover:

- Calendar timezone normalization, one-off versus recurring definitions, unknown properties,
  attachment references, and calendar/event relationships;
- Contacts, contact groups, membership relationships, unknown vCard properties, and external
  photo references;
- task lists, tasks, explicit completion evidence, parent/child relationships, and unknown fields;
- Saved list membership, source order, coordinates, and unknown CSV columns;
- My Maps KML contract behavior despite the product being absent from the real export;
- semantic parser-version upgrade behavior;
- relationship persistence, versioning, observations, reconciliation, and idempotent reruns.

Local verification before GitHub CI:

- 116 tests passed;
- 35 PostgreSQL integration tests are CI-gated;
- Ruff passed;
- all 29 Python modules compiled in memory;
- `personal_data_lab-0.6.0-py3-none-any.whl` built successfully with SHA-256
  `857AB697051427EF9BEE2B70F03B0B1DAEC9427C7793C2E3B7C670D2BBAC2D4F`.

## Sanitized real-data canary

The unchanged local Takeout was read only. No payloads, paths, inventories, logs, or database
contents are committed.

| Product | Members | Observed formats | Records | Events | Assets | Relationships | Failures | Unsupported |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| Timeline | 2 | JSON, TXT | 2 | 0 | 0 | 0 | 0 | 0 |
| Maps | 7 | CSV, JSON | 7 | 0 | 0 | 0 | 0 | 0 |
| Maps (your places) | 2 | JSON | 2 | 0 | 0 | 0 | 0 | 0 |
| My Maps | 0 | not present | 0 | 0 | 0 | 0 | 0 | 0 |
| Saved | 5 | CSV | 14 | 0 | 0 | 9 | 0 | 0 |
| Calendar | 6 | ICS | 63 | 54 | 10 | 67 | 0 | 0 |
| Contacts | 6 | VCF | 553 | 0 | 0 | 1,259 | 0 | 0 |
| Tasks | 1 | JSON | 2 | 0 | 0 | 1 | 0 | 0 |

The Timeline members in this export describe settings/notices rather than visit tracks; PR6 does
not manufacture visits that the source does not prove. My Maps is implemented with synthetic
coverage and must remain labeled `not_present_in_export` until a real canary exists.

## Remaining frontier

PR7 is Communications only: Mail, Google Chat, Voice, Google Meet, and Groups. MBOX and message
streams must remain bounded and provider-native message/attachment evidence must be retained.
