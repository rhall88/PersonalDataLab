# Official Apple privacy-export ingestion specification

## Scope and provenance boundary

This importer accepts untouched ZIP archives delivered by Apple's privacy portal. Its source is
`apple`, acquisition method is `apple_privacy_export`, and its snapshot scopes begin with
`account/apple_privacy/`.

Independently acquired iPhone, iMazing, RSMF, CSV, or Messages database exports are not inputs.
The retained iMazing CSV corpus is implemented by the separate `imessages` source family with
device-extraction provenance; RSMF and raw Messages database formats remain unclaimed. A provider
product name never overrides acquisition provenance. See
[`IMESSAGES_INGESTION_SPEC.md`](IMESSAGES_INGESTION_SPEC.md).

## Source safety

- ZIPs are opened read-only and are never extracted over the source directory.
- Every archive path is checked for absolute paths, drive prefixes, NULs, and parent traversal.
- Nested ZIPs are traversed recursively to a bounded depth using spooled temporary files.
- Duplicate names in each ZIP level are addressed by central-directory index, not by ambiguous name.
  Persisted `apple-zip:` locators encode every level and can be reopened with `open_apple_locator`.
- Binary bytes stay in the source archive; PostgreSQL stores only an external asset reference,
  content hash, byte count, media type, and provenance.
- Progress and default inventory output use opaque member IDs; the source directory path is also redacted. `inspect-apple --include-paths` is
  an explicit opt-in because Notes filenames can contain private note titles or content.

## Accounting contract

`inspect-apple` accounts for every recursively observed entry in exactly one disposition:

1. `classified`: a data leaf with one deterministic adapter (classification alone does not prove semantic support);
2. `structural`: a directory, nested ZIP container, or provider guide;
3. `unknown`: unsafe or unsupported content that prevents a successful production gate.

Only an exact allowlisted provider guide filename at the outer archive root is structural.
Names containing “guide”, documentation inside Notes, and all other PDFs are indexed as assets.

The report separates physical source bytes, recursive uncompressed bytes (including nested
containers), and logical leaf bytes (excluding container bytes). Nested compression is visible
without being double-counted as physical source data. Complete accounting requires
`classified + structural + unknown == recursive entries` and exact recursive and leaf byte sums.

## Representation and interpretation contract

The permanent registry contains the 32 Apple surfaces observed in the retained export. Present
surfaces route to one of these representations:

- CSV: bounded 5,000-row chunks with all headers, values, and surplus columns retained;
  duplicate/reserved headers use positional `headers`/`cells` payloads;
- JSON: arrays, objects, and Apple's observed concatenated top-level values;
- XML: complete root tree with attributes, untrimmed text/tails, ordered children, comments,
  and processing instructions within the root; raw bytes remain the lexical source of truth;
- VCF: contacts and groups with property values, parameters, and unfolded component lines retained;
- ICS: validated calendar/component boundaries, retained property parameters and nested components,
  recurrence overrides keyed by UID plus RECURRENCE-ID, and IANA TZID conversion. Floating local
  times and all-day dates remain provider values without invented UTC instants. Unknown zones
  emit warnings and block the production gate. Recurrence is never expanded;
- Notes text: exact-entry note records linked to stable directory containers; sibling media
  become external assets linked to those containers without guessing among multiple text files;
- other text: durable product records;
- audio, video, and images: streamed, content-addressed external assets.

Every normalized object uses source `apple`. Provider fields remain under `metadata.apple`; PDL
routing and acquisition facts remain under `metadata._pdl`. Durable namespaces include product,
dataset, and object kind so identifiers reused across Apple surfaces cannot collide. Provider IDs are used only by explicit dataset policies (currently account-device `devices` rows
with `Device Identifier`) and native VCF/ICS adapters. Generic activity rows use complete-payload
digests, never a device/account ID guessed to identify an event. Occurrence suffixes apply to exact
generic payload duplicates, not distinct activities. Duplicate entity IDs in mapped CSV datasets
fail explicitly rather than acquiring order-dependent identities.

Archive filenames, entry indices, and opaque member IDs are acquisition provenance, excluded from
canonical record/event metadata. They remain in source locations. Replay categories include stable
member/container context so identical bytes in different Notes directories cannot suppress notes
or attachment edges. Same-name ZIP occurrence ordinals distinguish duplicate paths while ignoring
unrelated central-directory reordering.

Apple account, Store, Music, Wallet, notification, and analytics rows that contain event/history
evidence and a provider time produce both a durable record and a normalized event. This retains
the provider object while making activity queryable. Unknown time formats remain in provider
metadata rather than being guessed.

Coverage deliberately reports two independent dimensions:

- `status` is the production representation gate. `fully_structured` identifies purpose-built
  representations; `losslessly_structured` identifies schema-agnostic structured representations.
  Both require every provider field to be retained in a deterministic normalized object (or a
  source-backed external asset), malformed input to fail closed, inspectable provenance, and exact
  replay safety.
- `interpretation_status` says how much provider meaning is projected. `provider_specific` is a
  purpose-built native/text/asset model; `generic_lossless` is the schema-agnostic CSV/JSON/XML
  representation described above. A product that mixes the two reports `generic_lossless`.

Generic lossless coverage is intentionally not a claim that every Apple product field has a
hand-written domain model. Those richer projections belong in the derived-understanding layer and
must cite the retained provider record. This distinction lets ingestion become production-ready
without relabeling inference as source truth.

## Restart and reconciliation

`import-apple-all` inventories before importing. Each data member, or each bounded chunk of a
large CSV, commits independently through the cumulative lossless reconciliation layer. A process
interruption preserves completed transactions; a rerun reuses them. Exact source/parser/category
runs short-circuit, including semantic member context in the category. Absence never creates deletion, and conflicts return exit code 2 without
discarding either representation.

Exit codes are `0` for success, `2` for conflicts, `3` for partial semantic support or undisposed
warnings, `4` for failed classified members, and `5` for unknown members. Precedence is failure,
unknown, conflict, partial/warning, success. Data represented with partial support is still persisted.

Coverage starts pending and is finalized from adapter support, parse warnings, and import outcome.
The generic CSV/JSON/XML adapter satisfies the production representation contract because it is
schema-agnostic and retains all decoded fields while the immutable source remains lexical truth.
It reports `status: losslessly_structured` and `interpretation_status: generic_lossless`, so callers
never confuse production-safe ingestion with complete product-specific interpretation.

## Production acceptance gate

Production-ready means all of the following are true for the retained corpus:

1. every recursive entry and byte is accounted;
2. unknown members and parse failures are zero;
3. all present products satisfy the production representation contract;
4. real-data parsing emits no undisposed warning;
5. a controlled interruption resumes without losing completed work;
6. a complete unchanged rerun short-circuits every data member and creates no new versions;
7. the complete PostgreSQL suite, unit suite, Ruff, and Python compilation pass;
8. no source payload, source path, generated inventory, log, or database is committed.


## Parser v3 acceptance and upgrade boundary

Parser v3 completed fresh real-corpus persistence, controlled interruption/resume, semantic
reconciliation, repair of valid newline-only TestFlight datasets, and exact unchanged replay in
PR16. It processed all 814 data leaves with zero failures, unknowns, warnings, or conflicts. See
[`PR16_APPLE_V3_PRODUCTION_ACCEPTANCE_REPORT.md`](PR16_APPLE_V3_PRODUCTION_ACCEPTANCE_REPORT.md).

PR16 is operational corpus evidence for the same parser bytes, but it predates the explicit split
between representation readiness and interpretation depth. At that milestone, 27 present product
groups were conservatively labeled partial solely because they used the generic structured
adapter; Business Chat Support, Store Credit, and Voice Memos were fully structured; Apple TV and
Podcast Information and Siri were absent. The later production-readiness acceptance reuses no
claim from an unparsed format: it qualifies the already tested CSV/JSON/XML representation and
keeps the 27 groups visibly `generic_lossless` rather than calling them provider-specific.

The historical PR14 corpus report measures parser v2 and cannot substitute for v3 evidence.
Re-importing v3 into an existing v2 database preserves historical rows and may add identities or
versions or report conflicts; it is not a destructive migration or cleanup. Validate upgrades in
a fresh isolated database first, and preserve the original corpus and historical database.
