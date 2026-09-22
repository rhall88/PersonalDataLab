# Phase 2 implementation plan: identity

## Summary

Phase 0 built the derivation runtime; Phase 1 made "what did I know as of import N" and "how
complete is my record of each source" answerable without touching canonical rows. Phase 2, per
`DERIVED_MEANING_ARCHITECTURE.md` section 15, is entity resolution: turn the raw, per-source
`actor_identity` strings already sitting on `events`/`records` into **people**, so that "who did I
talk to most, per year" is answerable across channels rather than once per source.

This is not a greenfield build. Migration 009 (2026-09-20, predating the derived-meaning
architecture entirely) already added `people`, `identity_links`, and `actor_person_id` on
`events`/`records`, plus five `identity-*` CLI verbs (`identity-candidates`, `identity-people`,
`identity-create-person`, `identity-merge`, `identity-split`). That is a working, reversible,
purely manual instance of the architecture's tier 3 ("You (DECLARED)"). Phase 2's job is not to
replace it but to grow tiers 1 and 2 around it, and to give the whole thing the append-only,
versioned foundation section 5 calls for ("the log is the source of truth; clusters are a derived,
versioned view") -- which `identity_links` today is not: it is a live current-state table with no
history of *why* a link was made beyond a free-text `note`.

A close read of the real `actor_identity` data (below) found that the architecture's tier-1
premise -- "a contact card listing a phone and an email; an OAuth ID tied to an email" -- only
partly holds here. This database has no contact-card import. What it has is highly
source-dependent, and inconsistent even within one source. That finding drives most of this
plan's scope decisions.

## What the repository already provides

- **`people`** (migration 002): `id`, `display_name`, `created_at`. Minimal by design -- no
  kind column, so today it implicitly means "person." One row exists in the real database.
- **`identity_links`** (migration 009): `(source, identity_value)` -> `person_id`, with
  `lifecycle_state IN ('active', 'reverted')` and a unique index on the active pair, so a value
  maps to at most one person at a time and a split reverts rather than deletes. Two active rows
  exist in the real database.
- **`events.actor_identity` / `records.actor_identity`** (migration 002, later joined by
  `actor_person_id` in migration 009): free-text, populated per-source by the importer, never
  normalized. `identity-candidates` already lists distinct values with occurrence counts and can
  exclude already-linked ones.
- **`conversations`**: no participant list column. A conversation's participants are the distinct
  `actor_identity` values on its `events`, joined by `conversation_id` -- there is no separate
  participants table to reconcile.
- **The derivation runtime** (Phase 0/1): registry, change feed, partition scheduling, egress
  lattice, `declare`/`measure` schemas, as-of reconstruction. Available if Phase 2's outputs ever
  need to ride it (see Decisions).

### What the real `actor_identity` data actually looks like

Queried directly against the real canonical database rather than assumed from the architecture
doc's examples:

| Source | Shape | Example |
| --- | --- | --- |
| `facebook` | Bare display name, no email/phone | `Ryan Hallgarth`, `Sarah Hallgarth`, `Amanda Kent` |
| `google` | RFC 5322 `"Name" <email>` for most rows; a `account_owner` sentinel for Ry's own sent mail | `LinkedIn <notifications-noreply@linkedin.com>`, `Ryan Hallgarth <rhallgarth88@gmail.com>` |
| `imessages` | **Not uniform even within one source**: regular `imessage_export`-namespace events carry a raw E.164 phone number (`+13173648707`); `imazing_csv/reaction` events carry an already-resolved display name (`Angela Barnes`) |
| `anthropic` / `openai` | Chat-turn role labels (`human`, `assistant`, `user`), not people at all; anthropic has 75 rows of an actual UUID (a workspace member id) |
| `apple` | `actor_identity` is entirely `NULL` -- Apple's export carries no per-row actor field |

Two consequences fall directly out of this:

1. **A per-source normalization strategy is wrong; per-value shape-sniffing is right.**
   iMessages alone proves a source can mix phone numbers and names depending on
   `source_record_namespace` and even record type. The right primitive is a function that looks
   at one `actor_identity` string and classifies it (`email`, `phone`, `name`, `role_label`,
   `opaque_id`), not a per-source table.
2. **A real cross-source, name-only match exists in the data today**: `lower(trim(actor_identity))
   = 'angela barnes'` appears in both `facebook` (a Messenger participant) and `imessages` (an
   iMazing-resolved reaction actor), 12 times combined. So does `'sara slaughter'`. Neither has a
   shared email or phone to corroborate it -- it is exactly the ambiguous case the architecture
   doc assigns to the Splink/STATISTICAL tier, not to hard rules. Two other apparent cross-source
   matches, `'you'` (facebook/imessages) and `'user'`/`'assistant'` (anthropic/openai), are role
   labels referring to no specific correspondent and must never be proposed as a person match.

## Corrections to the architecture doc

1. **Section 5's tier-1 examples ("phone and email on a contact card," "OAuth ID tied to an
   email") describe signal this database doesn't have.** There is no contacts import. The
   closest real hard identifiers are an email address *extracted from* google's From-header
   string, and an E.164 phone number *extracted from* imessages' raw participant field -- both
   real, both high-precision, but both require a parsing step the architecture doc doesn't
   mention, because it assumes structured contact data as an input that simply isn't in scope
   for the six real sources this project ingests.
2. **Exact name-string equality is not a tier-1 hard rule here, whatever it looks like
   syntactically.** It has no unique-identifier guarantee behind it (two different real people
   can share a name; the same person can be spelled two ways), and the one real example found in
   this database (`angela barnes`) has no corroborating identifier. Treating it as COMPUTED
   would violate the architecture's own stated priority ("a wrong merge is treated as worse than
   two entities left temporarily separate"). It belongs in the review queue, not the auto-link
   path -- see Scope.
3. **Section 15's exit test ("Top 200 correspondents verified") implicitly assumes the
   probabilistic tier is in scope for Phase 2.** Given point 2, a rules-only Phase 2 will
   under-merge real people who appear under an unrelated identifier per source with no shared
   email/phone (most of the `facebook`-only or `imessages`-only correspondents fall here, since
   neither carries the other's contact info for most people). This plan does not claim to fully
   satisfy that exit test; see Scope and Decisions.

## Scope

**In (Phase 2a, this plan):**
- `identity.assertion` (new schema): an append-only log of every identity assertion --
  `tier` (`computed` | `declared`), `action` (`link` | `unlink` | `not_same_as`), the
  `(source, identity_value)` and `person_id` involved, `evidence` (structured: which rule fired,
  or the existing free-text note), `resolution_version`, `created_at`. This becomes the
  source of truth `identity_links` was missing; `identity_links` becomes a materialized
  current-state projection of it, written in the same transaction.
- `identity.not_same_as`: a declared negative constraint on a `(source, identity_value)` pair (or
  a value and a person), so a rejected candidate never resurfaces. Required before any
  auto-linking exists, per the architecture's own description of its purpose.
- **Identifier shape-sniffing**: `identity/normalize.py` classifies one `actor_identity` string
  and, for `email`/`phone`, returns its normalized canonical form (lowercased email; E.164 phone
  reusing the existing digit-normalization the importer already does for imessages numbers on
  ingest, confirmed by reading `imessages` import code before writing new logic). A fixed
  `role_label` exclusion set (`you`, `user`, `assistant`, `system`, `human`, `facebook user`,
  `meta ai`, `account_owner`, more as review surfaces them) is skipped entirely -- never linked,
  never queued.
- **Rules tier (COMPUTED, auto-link)**: exact match on normalized `email` or `phone` values
  across two or more `(source, identity_value)` pairs not already governed by an active link or a
  `not_same_as` row. Creates a person (or extends an existing one) and writes both the
  `identity.assertion` row and the `identity_links` projection. This is the only tier that writes
  without a human in the loop, and only fires on the two identifier shapes that actually carry a
  uniqueness guarantee.
- **Cross-source name-candidate queue**: extends `identity-candidates` (or a new
  `identity-candidates --cross-source` mode) to surface exact normalized-name matches across
  sources -- the `angela barnes` case -- as candidates for `identity-merge`, not as auto-links.
  This is the queue tier 2 (Splink) would otherwise feed; for now it is fed by the one
  deterministic signal available.
- `resolution_version`: a monotonic counter bumped on every `identity.assertion` insert, stored on
  the assertion row. Exit test 2 below is about this, not about wiring resolution versions into
  the derive/partition machinery yet (see Decisions).
- Extend `identity-merge`/`identity-split` (unchanged CLI surface) to write through the new
  assertion log instead of writing `identity_links` directly.
- New CLI: `identity-not-same-as SOURCE IDENTITY_VALUE --person-id N` (or bare, meaning "never
  auto-link this value to anything"), `identity-rules-run` (executes the rules tier once,
  reads-then-writes, safe to re-run).

**Out (deferred to Phase 2b):**
- The Splink/Fellegi-Sunter probabilistic tier itself. The candidate queue above is a deliberately
  narrow stand-in fed by one deterministic signal (cross-source exact name match), not a scoring
  model. Splink needs co-participation, shared-thread and active-period features this plan does
  not build.
- Organizations, places, devices/accounts, projects, documents, and the `named_concept` entity
  kind (section 5's table). Phase 2a is people only, matching the architecture's own "people
  first" framing and this project's "single-user, scoped to Ry" convention carried over from
  Phase 0.
- Time-bounded identity membership (a phone number reassigned to a different person after a given
  date). `identity_links` has no temporal columns today and this plan does not add them; every
  link is treated as valid for the value's entire history. A real case of reassignment has not
  been found in this database yet.
- Wiring entity resolution into the derive/partition/worker machinery (a "resolution-keyed"
  partition class, downstream derivers going dirty when a person's cluster changes). Nothing in
  Phase 3+ depends on this yet since the life ledger (Phase 3) doesn't exist; premature to build
  the hook before there is a consumer.
- Satisfying the full "top 200 correspondents verified" exit test end-to-end. Phase 2a's own exit
  tests (below) are scoped to what rules-tier-plus-existing-declared-tier can actually deliver.

## Migration 012 (sketch)

`identity_links` and `people` are unchanged in shape; both gain a `resolution_version` reference,
and one new column on `identity_links` for provenance:

```sql
CREATE SCHEMA IF NOT EXISTS identity;

CREATE TABLE identity.assertion (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    resolution_version BIGINT NOT NULL,
    tier TEXT NOT NULL CHECK (tier IN ('computed', 'declared')),
    action TEXT NOT NULL CHECK (action IN ('link', 'unlink', 'not_same_as')),
    source TEXT NOT NULL,
    identity_value TEXT NOT NULL,
    person_id BIGINT REFERENCES people(id) ON DELETE SET NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE SEQUENCE identity.resolution_version_seq;

CREATE TABLE identity.not_same_as (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    identity_value TEXT NOT NULL,
    person_id BIGINT REFERENCES people(id) ON DELETE CASCADE,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source, identity_value, person_id)
);

ALTER TABLE identity_links ADD COLUMN resolution_version BIGINT;
ALTER TABLE identity_links ADD COLUMN tier TEXT NOT NULL DEFAULT 'declared'
    CHECK (tier IN ('computed', 'declared'));
```

`identity_links` keeps its existing unique-active-per-`(source, identity_value)` index and
`lifecycle_state` column unchanged -- it remains the fast current-state read path; `identity
.assertion` is the audit trail and the thing `not_same_as` and the rules tier actually consult.

## Python runtime: `personal_data_lab/identity/`

- `identity/normalize.py`: `classify(actor_identity: str) -> IdentifierShape` (`email`, `phone`,
  `name`, `role_label`, `opaque_id`), plus `normalize_email`/`normalize_phone`. The role-label set
  starts from the values found during this plan's own investigation and is easy to extend --
  documented as a living list, not a closed one, exactly like Phase 1's `observable_kinds`
  corrections needed a second look after the first pass.
- `identity/rules.py`: `run_rules_tier(connection) -> RulesOutcome` -- scans
  `events`/`records.actor_identity` not yet in `identity_links` or `identity.not_same_as`,
  classifies, groups by normalized `email`/`phone` value across sources, and for each group of
  size >= 2 spanning more than one source, links them to one person (creating it if none of the
  group is already linked; erroring loudly, not silently picking one, if the group's members are
  already linked to two *different* people -- that is a conflict for a human, not an auto-merge).
- `identity/candidates.py`: cross-source exact-name-match query, excluding role labels and
  already-declared `not_same_as` pairs -- feeds the extended `identity-candidates` CLI output.

## CLI

- `identity-rules-run [--dry-run]`: executes the rules tier once; `--dry-run` prints what would
  link without writing (mirrors no existing verb exactly, but matches the caution this tier
  deserves given it writes without a human).
- `identity-not-same-as SOURCE IDENTITY_VALUE [--person-id N]`: records a negative declaration.
- `identity-candidates --cross-source`: new flag on the existing verb, listing exact-name matches
  across sources as merge candidates rather than the existing single-source occurrence list.
- `identity-merge`/`identity-split`/`identity-people`/`identity-create-person`: unchanged CLI
  surface, now writing through `identity.assertion`.

## Delivery as three PRs

Same shape as Phase 0 and 1:

1. **PR A (schema):** migration 012 -- `identity` schema, `identity_links` additions. No runtime.
2. **PR B (runtime):** `identity/normalize.py`, `identity/rules.py`, `identity/candidates.py`, the
   CLI additions, and `tests/test_phase2.py` covering the exit tests below against
   `personaldata_test`.
3. **PR C (real-database rollout):** run `identity-rules-run` against the real database, review
   and act on the cross-source name-candidate queue (at minimum deciding `angela barnes` and
   `sara slaughter`), record results in `PROJECT_STATE.md`.

## Exit tests

1. **Rules tier links across sources on a shared normalized email/phone (synthetic).** Two
   `(source, identity_value)` pairs with the same email in different casing/whitespace, and two
   with the same phone in different formatting, each end up linked to one person; a
   `role_label`-classified value is never linked to anything.
2. **`resolution_version` is monotonic and recorded per assertion.** Three sequential
   merge/split/merge operations produce three assertion rows with strictly increasing
   `resolution_version`.
3. **A `not_same_as` declaration is permanent against the rules tier.** Declare it, then run the
   rules tier again with a matching email/phone pair present -- it does not link them.
4. **A rules-tier conflict (group already split across two people) raises, does not silently
   pick one.**
5. **Merge/split round-trips cleanly through the new log**: merge, split, merge again produces
   the correct final `identity_links` state and a full three-row `identity.assertion` history,
   not just the current-state table Phase 0-era tests would have checked.
6. **No canonical table (`events`, `records`, etc.) is altered by migration 012 or by
   `identity-rules-run`** -- structural assertion, matching every prior phase's own such check.
7. **(Rollout only, not automated)** `identity-rules-run` against the real database links at
   least one real cross-source pair by email or phone (none confirmed yet at plan-writing time --
   this is itself part of exit test 7, not assumed); the cross-source name-candidate queue
   surfaces `angela barnes` and `sara slaughter` and excludes `you`/`user`/`assistant`.

## Decisions

Ry delegated these calls; decided as follows, each with the reasoning that would otherwise have
been "leaning X" above:

1. **Exact name-string matches are queued for review, never auto-linked.** The one real example
   in this database has no corroborating identifier, and the architecture doc's own priority
   (wrong merge worse than two entities left separate) argues directly against treating name
   equality as a hard rule just because it's deterministic to compute.
2. **The full Splink/STATISTICAL tier is deferred to Phase 2b**, not attempted here. It needs
   co-participation and active-period features this plan doesn't build, and a narrow
   deterministic candidate queue (fed by cross-source name matches) is a real, honest interim
   step rather than a placeholder.
3. **`identity_links` is kept, not replaced.** It already has the right reversibility semantics
   (`lifecycle_state`, unique-active-index); `identity.assertion` is added underneath it as the
   log of record, written in the same transaction, rather than redesigning a table that already
   works.
4. **Phase 2a is people-only and has no time-bounded membership.** Matches the architecture's own
   "people first" framing and this project's single-user scope; no real identifier-reassignment
   case has been found in the data to justify the complexity yet.
5. **No hook into the derive/partition worker yet.** Nothing downstream (Phase 3's life ledger)
   exists to consume a "person cluster changed" dirty signal, so building the hook now would be
   speculative. Revisit when Phase 3 needs it.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_018XFC7J459MYNJGRRBjKcv9
