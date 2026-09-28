# Reversible identity resolution specification

## Scope

`personal-data-lab identity-candidates`, `identity-create-person`, `identity-people`,
`identity-merge`, and `identity-split` let a human assert, with inspectable evidence, that a
provider-observed identity value ("this string in this source") belongs to a real person — and
undo that assertion later. This is entirely manual and explicit: there is no fuzzy matching, no
automatic linking, and no heuristic guessing of who anything belongs to. It is the second piece of
the "Build query and understanding layers" roadmap phase, after full-text search
([`QUERY_SPEC.md`](QUERY_SPEC.md)). At the time this spec was written, embeddings, cross-source
correlation, and claims were future work described in [`COCKPIT_VISION.md`](COCKPIT_VISION.md);
that is no longer true. Embeddings shipped with Phase 6, and cross-source entity/relation
resolution plus claim evaluation are exactly what Phase 10's life graph does (schema built, real
extraction rollout in progress) — see `PROJECT_STATE.md`'s per-phase sections. Those are separate,
automatic/inferred mechanisms; nothing in *this* document's manual identity-assertion feature
creates or infers any of that, and this spec's scope is unchanged.

## Storage: a small table, not a retrofit

The original schema already carries dormant identity scaffolding — `people`, `identities`, and an
always-`NULL` `events.actor_person_id` column — none of it ever populated by any importer or CLI.
This feature reuses `people` as-is (its `id`/`display_name`/`created_at` shape already fits a
person record) but deliberately does not touch `identities`: that table predates every
versioning/lifecycle convention introduced in migrations 3–8 and has no way to represent a reverted
link, so retrofitting it risked bolting reversibility onto a table nothing was designed around. It
also deliberately does not extend the `relationships` table (migration 7): that table is
fingerprinted and authority-reconciled for *provider-evidenced* structural edges between canonical
objects, and its `subject_kind`/`object_kind` CHECK is closed over
`('conversation','event','record','asset')` — a fundamentally different trust model than a human
asserting "these are the same person."

Migration 9 instead adds one small purpose-built table, `identity_links` (`person_id`, `source`,
`identity_value`, `note`, `lifecycle_state`, `created_at`, `reverted_at`), plus
`records.actor_person_id` (fixing an existing asymmetry — `events` already had this column;
`records` never did). A partial unique index, `identity_links_active_idx ON identity_links (source,
identity_value) WHERE lifecycle_state = 'active'`, enforces exactly one active person per identity
value at a time. Splitting never deletes a row — it flips `lifecycle_state` to `'reverted'` — and
merging always inserts a new row rather than reusing an old reverted one, so the complete merge/
split history is permanent and inspectable, consistent with the append-only philosophy used
everywhere else in this schema (versions are never overwritten, tombstones are never deleted).

## Why writing `actor_person_id` is safe

`fingerprints.py`'s `event_canonical_payload`/`record_canonical_payload` functions enumerate every
field that participates in `content_fingerprint` explicitly, and `actor_person_id` is absent from
both — only the raw `actor_identity` text is fingerprinted. `canonical_revision`/
`canonical_updated_at` only advance inside `database.py`'s reconciliation branches when a
recomputed fingerprint changes. `merge_identity`/`split_identity` therefore perform a plain
parameterized `UPDATE ... SET actor_person_id = ...` entirely outside that machinery — it never
touches `content_fingerprint`, `canonical_revision`, or any `*_observations`/`*_versions` row.
`actor_identity` itself is never rewritten by this feature; provider-sourced text stays exactly as
imported, forever.

## What `actor_identity` actually contains today

Every source populates `actor_identity` (on `events` and `records` only) differently, which is
exactly the inconsistency this feature exists to resolve:

| Source | What `actor_identity` holds |
|---|---|
| OpenAI | A role label only (`"user"`/`"assistant"`/`"system"`/`"tool"`) — no personal identifier |
| Anthropic (messages) | A role label only (`"human"`/`"assistant"`) |
| Anthropic (account/project/login events) | A stable Anthropic account UUID string |
| Google (most products) | The literal constant `"account_owner"` — always the export owner |
| Google (Gmail) | The raw, unparsed RFC 5322 `From:` header text |
| Google (Chat) | An email address when present, else a display name |
| Facebook | A free-text sender display name — no stable numeric user ID |
| iMessages | A phone number, email/iCloud ID, or `name:<display name>` fallback (the most structured of the six) |
| Apple | Nothing — Apple's only person-shaped data is unparsed vCard `EMAIL`/`TEL` inside contact `records.metadata`, out of scope for this feature (see Known limitations) |

## Merge is idempotent, not a one-shot insert

There is no importer hook that calls `merge_identity` automatically, and none is planned — new
imports never retroactively get linked on their own. **Re-running `identity-merge` for a value
already linked to the same person is the documented way to backfill evidence imported since the
original merge.** It does not create a duplicate `identity_links` row (the partial unique index
would reject that anyway); it simply re-scans `events`/`records` and annotates whatever wasn't
already annotated, reporting `events_updated`/`records_updated: 0` if nothing changed. Merging a
value already actively linked to a *different* person is rejected — `identity-split` it first.

Evidence checking and backfill are deliberately lifecycle-state-agnostic (unlike search's
active-only default): a `provider_deleted` (tombstoned) row is still real evidence that the value
occurred, so both the "does evidence exist" check and the backfill `UPDATE` match it too.

## CLI

```powershell
personal-data-lab identity-candidates --format table
personal-data-lab identity-candidates --source google --min-count 5
personal-data-lab identity-create-person "Jane Doe"
personal-data-lab identity-merge 1 google "jane@example.com" --note "Gmail From header"
personal-data-lab identity-people --format table
personal-data-lab identity-split google "jane@example.com"
```

`identity-candidates` defaults to hiding already-linked values (`--include-linked` shows
everything), summing occurrence counts for a value across `events` and `records`. All five
commands use the same `_resolve_guarded_engine(..., canonical_import=False)` read/administrative
path as `search` and `migrate`. `identity-merge`/`identity-split` print `error: <message>` to
stderr and exit 1 on invalid input (unknown person, no evidence, already linked elsewhere, nothing
to split); success prints the resulting `IdentityLink` as JSON and exits 0.

## Known limitations

- Apple's contact vCard `EMAIL`/`TEL` fields are not extracted into `actor_identity` by any current
  adapter, so Apple contact evidence is invisible to `identity-candidates`/`identity-merge` in this
  version — a future enhancement could parse `records.metadata->'properties'` for Apple contacts.
- Facebook's `actor_identity` is a free-text display name with no stable numeric ID; two distinct
  real people can share a display name with no way for this tool to tell them apart. Every merge is
  a human assertion, not a guarantee of correctness.
- There is no fuzzy or automatic matching of any kind. A typo'd `identity_value` simply won't match
  any evidence and the merge will be rejected.
- New evidence for an already-linked identity is only picked up when `identity-merge` is re-run for
  that value; nothing re-scans automatically after an import.

## Verification

- Unit/integration suite (`tests/test_identity_resolution.py`, requires `TEST_DATABASE_URL`):
  candidate counts summed across events and records, default exclusion/`--include-linked` of
  already-linked values, source/min-count filtering, person create/list/get, merge backfilling
  `actor_person_id` on both tables, merge rejecting an unknown person, merge rejecting missing
  evidence, merge rejecting re-linking an actively-linked value to a different person, merge being
  idempotent and backfilling newly-imported evidence without a duplicate row, merge annotating
  tombstoned rows too, split reverting a link and nulling `actor_person_id` while preserving the old
  row, split rejecting when nothing is active, a re-merge after split inserting a new row while the
  old one stays reverted, and CLI smoke tests for all five commands including the error/exit-1 path.
- Manually verified against the real canonical database (see [`PROJECT_STATE.md`](PROJECT_STATE.md)
  for the migration rollout evidence and example real merge/split results).
