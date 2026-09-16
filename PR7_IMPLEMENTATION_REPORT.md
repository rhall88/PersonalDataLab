# PR7 implementation report: Google communications

## Scope

PR7 implements the contracted communications phase only: Mail, Google Chat, Voice, Google Meet,
and Groups. It does not begin Apple/iMessages or any PR8–PR11 product family.

## Implementation

- `google_takeout.communications` provides semantic parsers for the five products.
- Gmail MBOX processing is genuinely bounded: a first streaming pass computes the exact source
  SHA-256 and a second streaming pass emits deterministic 250-message chunks.
- Each chunk is a separate transaction/run category, while member and byte coverage are counted
  once. Interrupted runs can restart without treating completed chunks as new source members.
- Thread/message, message/attachment, Chat membership, and Chat attachment relationships use the
  versioned relationship history introduced by migration 007.
- MIME and provider-native fields remain in lossless normalized metadata. Binary attachments and
  Voice/Chat media remain external assets; no binary payload is stored in PostgreSQL.
- An individual MBOX message larger than 128 MiB becomes an explicit oversized-message event and
  warning instead of causing unbounded memory use or silent omission.

## Real-export acceptance

The complete observed PR7 family was parsed read-only from the local Takeout corpus:

| Product | Members | Source bytes | Records | Events | Assets | Relationships | Failures / unsupported |
|---|---:|---:|---:|---:|---:|---:|---:|
| Mail | 3 | 4,766,078,794 | 44,147 | 47,277 | 2,672 | 49,949 | 0 / 0 |
| Google Chat | 13 | 1,279,749 | 406 | 403 | 16 | 411 | 0 / 0 |
| Voice | 139 | 1,542,068 | 1 | 127 | 11 | 0 | 0 / 0 |
| Google Meet | 2 | 1,194 | 0 | 5 | 0 | 0 | 0 / 0 |
| Groups | 3 | 216 | 7 | 0 | 0 | 0 | 0 / 0 |

The 4.77 GB Gmail source produced 192 bounded parse chunks with a largest chunk of 1,419
normalized objects. The acceptance run completed with zero parser warnings, failures, and
unsupported members and remained below 200 MB working memory.

## Automated coverage

PR7 tests cover MBOX batching, exact source hashing, MIME attachments, thread relationships,
oversized-message handling, Chat messages/membership/assets, Meet calls, Groups membership,
Voice HTML/VCF/media, and orchestrator accounting across multiple chunks. The existing PR1–PR6
suite remains part of the same gate.

## Next frontier

PR8 is exactly the rich-asset phase: Google Photos, Drive, Recorder, Phone Audio, and Street View.
It must retain sidecar/document/transcript semantics while indexing binary assets externally and
must not introduce database binary bloat.
