# PR8 implementation report: Photos, Drive, and rich assets

## Scope

PR8 implements Google Photos, Drive, Recorder, Phone Audio, and Street View. It contains no Apple
or iMessages work and does not pull PR9 knowledge/AI products forward.

## Implementation

- Photos media sidecars become lossless photo/video metadata records linked by a stable product
  path identity to their media assets, even when sidecar and media arrive in different ZIP parts.
- Album entry arrays produce explicit ordered album-to-asset relationships.
- Drive structured files retain row/object fields and bounded text projections. DOCX, PPTX, and
  XLSX text is extracted directly from their safe Office Open XML parts.
- Recorder transcript files become transcript records linked to the corresponding M4A path.
- Every remaining media/document/archive member is SHA-256 streamed into an external asset
  reference. Content bytes are never inserted into PostgreSQL.
- Text/structure parsing is capped at 64 MiB per member and text projections at 2 MiB. Larger
  structured files fall back to external asset indexing rather than being silently omitted.
- Nested My Activity exports stored inside Drive continue to route to the PR4 streaming parser
  without adapter ambiguity.

## Real-export acceptance

Inventory accounts for the complete observed family:

| Product | Inventory | Representative semantic/asset canary | Result |
|---|---:|---:|---|
| Google Photos | 22,670 members / 120,339,867,356 bytes | 123 members / 301,817,866 bytes across every observed format | 100 records, 23 assets, 108 relationships, 0 failures |
| Drive | 411 members / 2,835,021,323 bytes | 204 members / 634,188,928 bytes, including all observed structured/Office files | 51,976 records, 236 nested activity events, 202 assets, 51,976 relationships, 0 failures |
| Recorder | 86 members / 1,707,945,303 bytes | all 41 transcripts plus representative audio | 41 records, 43 assets, 41 relationships, 0 failures |
| Phone Audio | absent | synthetic format contracts | not present in export |
| Street View | absent | synthetic format contracts | not present in export |

The five Drive warnings are explicit 2 MiB normalized-text truncations; the complete provider
rows remain in metadata and the immutable source remains externally referenced.

## Next frontier

PR9 is exactly AI/knowledge/notes: Gemini, Gemini in Workspace, NotebookLM, Flow, Workspace Studio,
Keep, Journal, Pinpoint, and Google Cloud Search.
