# Database & Local State — Ground Truth (as of 2026-09-19)

## Update 2026-09-19 (later same day): canonical rebuild executed for all five sources

Everything below this note describes the state **before** the rebuild this section records. Steps
4 and 5 of the "Proposed sequence" below are now done: `pdl_canonical_postgres_data` was created as
the new dedicated volume, `compose.yaml`/`.env` were pointed at it, and `personaldata` was rebuilt
from raw exports only, one source at a time. Anthropic, OpenAI, and iMessages were already correct
and complete (verified against their PR19/PR17/PR15 accepted baselines with zero discrepancy) before
Apple and Google were added today:

- **Apple**: 897/897 import runs completed, `production_ready: true`, exit 0. 476,444 records,
  162,318 events, 380 assets, 161 relationships — exact match to
  [`APPLE_PRODUCTION_READINESS_ACCEPTANCE_REPORT.md`](APPLE_PRODUCTION_READINESS_ACCEPTANCE_REPORT.md).
  This resumed cleanly after an unrelated local power interruption stopped it at 772/897 runs; the
  restart-safe per-member transaction design meant no row was left partially written, and the
  identical command completed the remaining 125 members.
- **Google**: one fresh complete pass, 25,427/25,427 data members, 51/51 registry products routed,
  zero failed/unknown members, all 146,248,662,729 bytes accounted, exit 0, in 1h19m34s. 122,293
  records, 164,032 events, 13,983 assets, 115,221 relationships. This differs from the
  `google_acceptance_test` snapshot recorded below by +657 records, +657 relationships, and +1
  event — expected, because that snapshot predates PR13's later differential-replay fixes and this
  run used the fully-fixed current importer directly against the canonical database.

See [`PROJECT_STATE.md`](PROJECT_STATE.md) for the consolidated table.

**Update, same day: step 6 (idempotency check) is now done.** Apple and Google were each re-run a
second time, identically, against this combined canonical database (all five sources present, not
one in isolation):

- Apple: all 814/814 members short-circuited, zero new records, exit 0, `production_ready: true`.
  Canonical counts unchanged: 897 runs, 476,444 records, 162,318 events, 380 assets,
  161 relationships.
- Google: all 25,427/25,427 members short-circuited, zero failed/unknown, exit 0. Canonical counts
  unchanged: 23,333 runs, 122,293 records, 164,032 events, 13,983 assets, 115,221 relationships.

Zero drift in either source. The canonical-rebuild proposal below is now fully executed and
verified, not just executed.

## Update 2026-09-20: Facebook added as a sixth canonical source

A new `facebook_export` module (see [`FACEBOOK_PRODUCTION_ACCEPTANCE_REPORT.md`](FACEBOOK_PRODUCTION_ACCEPTANCE_REPORT.md))
was accepted against an isolated database, then imported into this same canonical `personaldata`
database: 2,673/2,673 members, exit 0, `production_ready: true`, 574 conversations, 43,051 records,
91,525 events, 1,786 assets, 1,309 relationships — identical to the isolated acceptance run. A
second identical pass short-circuited all 2,673 members with zero drift, matching the idempotency
verification already done for Apple and Google above. All six source families (Anthropic, OpenAI,
iMessages, Apple, Google, Facebook) now coexist in this one canonical database. See
[`PROJECT_STATE.md`](PROJECT_STATE.md) for the consolidated table.

## Update 2026-09-20: Google Nest recovery fix landed in the canonical database

A provider double-escaping bug in Nest's CSV-wrapped JSONL (see [`ROADMAP.md`](ROADMAP.md)) was
fixed and re-imported against this canonical database: all 25,427 Google data members, exit 0,
zero failures/unknowns/conflicts. `nest_event` rows went from 35 (31 stored as opaque text) to 66
— the original 31 opaque rows are still there, untouched, and 31 new rows now carry the recovered
structured content, per the immutable-version contract (corrected content is added, never
overwritten). Reprocessing required bumping `AccountCommerceStructuredAdapter`'s parser version
first, because the restart-safe run-key includes it — the first re-import attempt short-circuited
every member and silently did not apply the fix.

**Not done**: a second idempotency-verification pass (re-running again to confirm exact replay)
was explicitly deferred as low priority for this fix, unlike Apple, Google's original import, and
Facebook, all of which have that verification. If Google's exact-replay guarantee needs to be
re-confirmed for another reason, do that pass then.

This file exists because local operational state (Docker volumes, databases,
raw export locations) had drifted into something no one — human or model —
could read correctly on first contact.

**Tagging convention used throughout, per explicit instruction:**
- **[VERIFIED]** — confirmed this session by direct tool inspection (`docker
  inspect`, `psql` queries, `pg_restore --list`, file listings, `strings` on
  raw catalog pages, or a real PR body fetched via `gh`).
- **[SPOT-CHECKED]** — a claim relayed from prior context (ChatGPT/Codex
  history) that was independently cross-checked against a primary source
  (a real PR body or a live query) and matched.
- **[UNVERIFIED CLAIM]** — relayed from prior context, plausible and
  internally consistent with verified facts, but not independently confirmed
  this session. Treat as a lead, not a fact.
- **[DECISION / TARGET STATE]** — a proposed future state, not current reality.

If this file and a PR's acceptance report disagree, re-verify — don't trust
either blindly.

## The three concepts that got blurred together **[VERIFIED — architectural pattern, confirmed by direct inspection of all 15 databases]**

1. **Raw exports** — immutable source data from each provider. Ground truth.
2. **Acceptance-test databases** — ephemeral, created per PR/run to prove an
   importer works against real data. **[DECISION / POLICY, not a verified
   fact about the past]**: intended to be disposable once verified. The
   current sprawl proves many databases were created for exactly this
   purpose — it does not prove every historical run was intended to be
   deleted immediately after. "Disposable" is the policy this project should
   enforce going forward, not an established fact about prior runs.
3. **The canonical corpus** — one durable database with all sources
   reconciled together. **This does not exist yet.** Every "production
   acceptance" PR proved its importer against its own disposable database;
   none of that was ever consolidated.

**[SPOT-CHECKED via PR #14 body]**: this is not a guess about intent — PR #14
itself states *"The previous 31-archive corpus import and exact replay
measured parser v2... those historical numbers remain documented, but are
not evidence for parser v3."* The project's own PR history already
establishes "prefer current importer over historical acceptance data" as a
first-party principle, not an outside inference.

## Original architectural intent **[UNVERIFIED CLAIM, relayed — internally consistent with PR #1 and PR #3 titles/scope, not independently re-derived from their full diffs]**

Per relayed history (PR #1 "first vertical slice", PR #3 "lossless cumulative
canonical imports"): the system was meant to maintain one cumulative
canonical corpus where overlapping exports insert new records, update
changed ones, skip unchanged ones, and never delete older unique data just
because a later export omits it. Raw exports were always meant to stay
immutable. **[SPOT-CHECKED]** PR #1's real body confirms canonical
conversations/events across repeated exports, many-to-many source-file
provenance, and an explicit central invariant example — this matches the
claim.

**OpenAI was the literal original ingestion target** — **[SPOT-CHECKED]**,
confirmed directly from PR #1's real body: *"OpenAI export importer for
ZIPs, extracted directories, and conversations.json"* was in-scope from
commit one. **The absence of any OpenAI database in current Docker state is
not evidence OpenAI was never implemented or run** — see the dedicated
section below.

## Raw exports (immutable — never delete) **[VERIFIED — file listing, 2026-09-19]**

All under `C:\Users\Ryan\Desktop\DataLabData\`:

| Folder | Contents | Size |
|---|---|---|
| `Google\` | 9 Takeout zip parts (`takeout-20260909T012526Z-*.zip`) | ~132.3 GiB |
| `Apple\` | ~30 zip files — **[VERIFIED]** two overlapping export batches (friendly-named + slug-named duplicates of the same categories); not yet reconciled into one clean set | ~1.4 GB |
| `OpenAI\` | 1 zip | 2.4 GB |
| `iMesages\` | 2 CSVs (`Messages - 48 chat sessions.csv`, `Messages - 56 chat sessions.csv`) — **note the typo in the folder name; do not "fix" it silently, see Open Questions** | ~11.6 MB |
| `Anthropic\` | manifest + 5 category zips | ~19 MB |
| `Anthropic\` ↔ root | `projects-000.zip` is manifest-declared but sits at `DataLabData\` root, **not** beside the manifest. **Unresolved. Do not move.** |

## Docker — what's actually running **[VERIFIED]**

| Container | Volume | Status |
|---|---|---|
| `personal-data-db` | `repo_postgres_data` (~5.05 GB) | Running, compose-managed, `.env`/`DATABASE_URL` currently target it |
| `pdl-pr13-db` | `pdl_pr13_postgres_data` (~12.26 GB) | Stopped (returned to original state after inspection) |

Five Docker volumes total exist **[VERIFIED]**:
`repo_postgres_data`, `pdl_pr13_postgres_data`, `personaldatalab_postgres_data`
(genuinely empty, 4 KB, never used — confirmed by direct read-only mount),
and two anonymous volumes (`4d64ce04...`, `b8c39853...`) — **[VERIFIED]**
both contain a real but generic Postgres cluster with exactly one extra
database named `pdl_test` (read directly from raw catalog pages via
`strings`, no credentials needed), created 2026-09-11. **Ruled out as
OpenAI-related** on both date (predates PR #17 by a week) and size (42–83 MB
vs. the 267 MB PR #17's own report documents). No other Docker context,
container, or volume exists on this machine **[VERIFIED — `docker context
ls`, `docker ps -a` unfiltered]**.

VHDX backing everything: `docker_data.vhdx`, ~20.5 GiB **[VERIFIED]**.

`.env` (gitignored) was missing entirely before this session; reconstructed
2026-09-19 from the live container's own environment. It currently points at
`personaldata_test`, which is nearly empty. **The CLI's default target is
not the real data.** **[VERIFIED]**

## Did OpenAI ever get imported? **[VERIFIED — yes; intentionally ephemeral, not present locally]**

Direct answer, not an absence-of-evidence shrug: **yes**, per PR #17's own
acceptance report (`PR17_OPENAI_PRODUCTION_ACCEPTANCE_REPORT.md`), which
documents an exact final state — 1,187 conversations, 25,510 events, 1,135
records, 2,639 assets, 27,602 relationships, **267,007,679 bytes in the
final PostgreSQL acceptance database** — plus a full interruption/resume
cycle and an exact-replay pass, all with exit code 0.

The same report states explicitly: *"No source payload, conversation
content, title, source path, generated inventory, runtime log, probe, or
database is committed."* That database was **intentionally ephemeral by
design**, consistent with this project's stated privacy/evidence-preservation
methodology — it is not lost evidence, it worked as intended. It is not
recoverable from current local Docker state. This was checked exhaustively
this session: all 15 live databases enumerated, both anonymous orphaned
volumes opened and ruled out by content/date/size, full git history searched
for literal OpenAI database-name strings (zero hits — nothing was ever
hardcoded to source control), and the Codex artifact folders searched (no
distinct acceptance-report files found there beyond what's already in git).

**To get a real OpenAI corpus into the canonical DB, it must be reimported
fresh from `DataLabData\OpenAI\` via the current importer** — there is no
shortcut through old state.

## Every database found (15 total) — full inventory **[VERIFIED — all rows below from direct `psql` queries, 2026-09-19]**

### On `personal-data-db` / `repo_postgres_data`

| Database | Size | Schema | Source(s) | Files | Runs | Canonical rows | Exported_at | Likely origin | Regenerable? |
|---|---|---|---|---|---|---|---|---|---|
| `personaldata` | 72 MB | **v2–5 only** (predates migrations 006/007 — no `records`/`assets`/`relationships` tables at all, confirmed via `\dt`, 18 tables only) | anthropic | 5 | 1 completed, 2026-09-11 | conversations/events/attachments only (old schema) | — | Manual dev DB from the PR2/PR3 era, before Google Takeout framework (PR5) generalized the schema. **Frozen — do not "upgrade in place."** | Yes via current `import-anthropic-all`, but confirm first it holds nothing unique |
| `personaldata_anthropic_acceptance_test` | 94 MB | v7 | anthropic | 6 | 18 completed, all 2026-09-19 | 269 conv / 9,748 events / 10,014 rel / 601 assets / 1 record | — | PR #19 acceptance run (merged 2026-09-19T05:22Z) | Yes — current-importer run |
| `personaldata_apple_readiness_test` | 3.6 GB | v7 | apple | 805 | 897 completed, 2026-09-19 02:56–03:57 | 476,444 records / 162,318 events / 380 assets / 161 rel | — | Run **after** PR #16's Apple-empty-dataset fix (merged 2026-09-18T14:56Z) — most trustworthy Apple run | Yes |
| `personaldata_test` | 16 MB | v7 | imessages | 2 | 2 completed, 2026-09-19 | 1 conv / 2 events (source files are 275–293 byte synthetic fixtures, **not** the real CSVs — confirmed via `source_files` query) | — | pytest fixture residue on the live compose DB | N/A — not a real corpus run |

### On `pdl-pr13-db` / `pdl_pr13_postgres_data` (all v7 schema)

| Database | Size | Source | Files | Runs | Canonical rows | Exported_at | Likely origin | Regenerable? |
|---|---|---|---|---|---|---|---|---|
| `personaldata_apple_acceptance_test` | 3635 MB | apple | 458 | 695 ok + 10 conflicted, 2026-09-17 05:44–06:47 | 476,574 records / 162,318 events / 33 assets / 167 rel | — | PR #14 era, **before** PR #16's fix | Likely superseded — aggregate-count comparison only, not row-level proof; **not yet cleared as safe to delete** |
| `personaldata_apple_final_test` | 3628 MB | apple | 458 | 546 completed, 2026-09-17 06:48–07:49 | 476,439 records / 162,318 events / 33 assets / 156 rel | — | Same PR #14-era, pre-fix | Likely superseded — same caveat as above |
| `personaldata_apple_suite_test` | 12 MB | — | 0 | 0 | all zero | — | Empty scratch DB | N/A |
| `personaldata_google_acceptance_test` | 2143 MB | google | 22,993 | 22,675 ok + 658 conflicted, 2026-09-17 | 121,636 records / 164,031 events / 13,983 assets / 114,564 rel | **2026-09-09 01:25:26Z** (matches real Takeout filename timestamp exactly) | PR #13 full acceptance run (merged 2026-09-17T05:06Z) | Backed up **and restore-drill verified**, see below |
| `personaldata_imessages_acceptance_test` | 447 MB | imessages | 2 | 35 completed, 2026-09-17 22:44–22:57 | 103 conv / 71,789 events / 3,136 assets / 75,928 rel | — | PR #15 acceptance run (merged 2026-09-17T23:26Z). **[SPOT-CHECKED]** these exact figures match relayed historical context independently | Likely a duplicate of `v2_final_test` — see uniqueness note below |
| `personaldata_imessages_restart_test` | 121 MB | imessages | 1 | 9 completed, 2026-09-17 22:58–23:01 | 48 conv / 18,226 events / 130 assets / 18,481 rel | — | PR #15 interruption/resume verification (single file, intentionally partial) | N/A — test-specific |
| `personaldata_imessages_suite_test` | 12 MB | imessages | 2 | 2 completed | 1 conv / 2 events | — | pytest fixture residue | N/A |
| `personaldata_imessages_v2_final_test` | 447 MB | imessages | 2 | 35 completed, 2026-09-17 23:03–23:17 | matches every aggregate count in `imessages_acceptance_test` — see uniqueness note | — | PR #15 final pre-merge rerun | Backed up **and restore-drill verified**, see below |
| `personaldata_pr13_exact_test` | 27 MB | google | 379 | 392 completed | 855 records / 500 events / 29 assets / 907 rel | 2026-09-09 01:25:26Z | PR #13 dev-stage subset | Likely superseded by `google_acceptance_test` — aggregate comparison only |
| `personaldata_pr13_suite_test` | 12 MB | — | 0 | 0 | all zero | — | Empty scratch DB | N/A |
| `personaldata_pr13_targeted_test` | 516 MB | google | 690 | 709 ok + 13 conflicted | 58,966 records / 8,951 events / 940 assets / 60,808 rel | 2026-09-09 01:25:26Z | PR #13 dev-stage subset, larger | Likely superseded — same caveat |

**Uniqueness assessment [VERIFIED at aggregate-count level only — NOT
row-level or hash diffed. Read carefully: "identical aggregate counts" is
strong evidence of duplication, it is not proof of byte-identical database
content.]** `imessages_acceptance_test` and `imessages_v2_final_test` match
on every measured aggregate (conversations, events, assets, relationships,
records) — likely duplicates, not confirmed identical at the row level.
The three Apple databases differ by only ~100–200 records out of ~476,000 —
consistent with re-runs of the same corpus, not distinct data;
`readiness_test` is the one to trust (post-fix), but "likely superseded" is
as far as the evidence goes — it is **not** "proven safe to delete." The
Google trio (`exact_test` → `targeted_test` → `google_acceptance_test`)
shows monotonically increasing coverage (855 → 58,966 → 121,636 records) —
consistent with staged dev-scope expansion during PR #13, not independent
corpora, same caveat applies. **Row-level hash comparison has not been
performed** — that is the next concrete check before any deletion, not an
assumption to act on now.

## Backups taken 2026-09-19 **[VERIFIED — every dump checked with `pg_restore --list`, not just exit-code-trusted]**

Location: `C:\Users\Ryan\Desktop\DataLabBackups\2026-09-19\` (outside the
Docker VHDX).

| File | Size | SHA-256 |
|---|---|---|
| `personaldata.pg_dump` | 21,432,236 B | `e03b85f9...668eb41` |
| `personaldata_anthropic_acceptance_test.pg_dump` | 23,537,951 B | `e16ac22d...cf5900` |
| `personaldata_apple_readiness_test.pg_dump` | 499,638,239 B | `87cd98c3...5dca2f0c` |
| `personaldata_google_acceptance_test.pg_dump` | 691,350,703 B | `23a9e1a2...446d87` |
| `personaldata_imessages_v2_final_test.pg_dump` | 43,279,902 B | `226ccc2b...ffda631` |
| `personaldata_test.pg_dump` | 130,695 B | `215f2af4...060ff8cf` |
| `globals.sql` (personal-data-db instance roles) | 691 B | `50c68836...c79f6efa2b` |
| `globals_pdl-pr13-db.sql` (pdl-pr13-db instance roles) | 691 B | `06f51fba...305930d8c94d6601` |

All `pg_restore --list` runs returned 141–249 TOC entries, no errors.

### Disaster-recovery restore drill — **[VERIFIED, executed 2026-09-19]**

Performed **before** any canonical-rebuild design work, per explicit
instruction — a backup that's never been restored is unverified, not a real
safety net.

Procedure: created a brand-new disposable Postgres 18/pgvector instance
(fresh Docker volume `pdl_restore_drill_data`, fresh role `drill_admin`,
zero relation to either `personal-data-db` or `pdl-pr13-db`). Copied all 6
dump files in by their recorded SHA-256 above, `CREATE DATABASE` + `pg_restore`
for each, then re-ran the same schema-version / source-file / import-run /
canonical-row-count queries used to build the inventory above.

**Result: every restored database matched its original, field for field,
exactly** — schema version, file counts, run counts, and all five canonical
row counts (conversations/events/records/assets/relationships) for
`google_acceptance_test`, `apple_readiness_test`, `imessages_v2_final_test`,
`anthropic_acceptance_test`, plus the older-schema `personaldata` (schema
max version 5, 5 anthropic files) and `personaldata_test` (schema v7, 2
imessages files). Restore produced only expected, non-fatal ownership
warnings (`ALTER TABLE ... OWNER TO personaldata_admin` — that role doesn't
exist in the disposable instance; data and schema were unaffected: 30
ignored errors for each v7-schema DB, 18 for the older-schema `personaldata`,
consistent with each one's table count).

The drill container and volume were torn down immediately after
verification — Docker is back to its exact pre-drill state (confirmed via
`docker ps -a` / `docker volume ls`).

**Methodological note:** the restore commands were piped through `tail -5`
for readability, which meant the `$?` captured and logged as "restore exit
for X" was `tail`'s exit code, not `pg_restore`'s — those logged exit codes
are meaningless and should be ignored. The verification that actually
matters — schema version, source-file counts, import-run counts, and all
five canonical row counts matching exactly — was obtained independently via
direct `psql` queries against the restored databases, not from trusting
`pg_restore`'s exit status.

**Restore verification, precisely stated:** all six custom-format backups
were restored into a fresh, independent PostgreSQL 18 instance. Schema
versions, source-file counts, import-run counts, provenance, and canonical
row counts matched the original databases exactly. Restores emitted expected
ownership warnings because source roles (`personaldata_admin`) were not
recreated in the disposable drill instance. **Therefore: the dumps are
verified for logical data/schema recovery. They are not yet verified for a
clean, zero-error full-environment recovery of roles/ownership/privileges.**
A future full DR drill should restore instance globals/roles first (the
`globals.sql`/`globals_pdl-pr13-db.sql` dumps already captured above cover
this) — or explicitly use a role-remapping / `--no-owner` recovery strategy
— to validate ownership and privileges as well, before this is called a
complete disaster-recovery guarantee rather than a data-integrity one.

Not backed up (deferred, explicitly out of scope this pass): the 3
Apple/Google databases flagged "likely superseded" above, and 2 empty
scratch databases on `pdl-pr13-db`.

## Do-not-delete list (current) **[DECISION / current policy, not yet automated]**

> **Update 2026-09-29:** items 2 and 4 are retired. Every database in `repo_postgres_data`
> (4) and `pdl_pr13_postgres_data` (11) was dumped with `pg_dump -Fc`, each dump re-read with
> `pg_restore --list` from its copy on disk (141-249 TOC entries, zero errors), and recorded
> with byte size and SHA-256 in `C:\Users\Ryan\Desktop\DataLabBackups\2026-09-29-retired-volumes\MANIFEST.tsv`
> (plus each instance's `globals_*.sql`). 2.3 GB of dumps replace 17.2 GB of volumes. Five dumps
> are byte-identical in size to the 2026-09-19 dumps, confirming those databases were untouched
> since. The volumes, the stopped `pdl-pr13-db` container and the empty
> `personaldatalab_postgres_data` were then removed. Item 5 (the two anonymous `pdl_test`
> volumes, 136 MB) is still kept. Item 1 (`DataLabData\`) and item 3 are unchanged: raw exports
> are never deleted.

1. Everything under `DataLabData\` — raw exports, irreplaceable.
2. `repo_postgres_data` and `pdl_pr13_postgres_data` — until the canonical DB
   is rebuilt and validated against them.
3. Everything in `DataLabBackups\2026-09-19\` above.
4. `personaldata` (the old DB) — until confirmed it holds nothing the
   current importer wouldn't reproduce.
5. Both anonymous volumes — low priority (generic `pdl_test` content,
   already identified) but not yet explicitly cleared for deletion.

## Proposed canonical rebuild architecture **[DECISION / TARGET STATE — proposed only, nothing below has been executed]**

```
DataLabData/                    <- raw exports, immutable, untouched by imports
  Google/ Apple/ Anthropic/ OpenAI/ iMesages/

pdl_canonical_postgres_data     <- NEW, DEDICATED Docker volume. Never
  (Docker volume)                  repo_postgres_data — that volume already
  └── personaldata                 carries historical garbage and acceptance
                                    residue (personaldata's stale v2–5 data,
                                    the empty personaldata_test, etc). A
                                    fresh volume means the canonical corpus
                                    has never shared physical storage with
                                    the archaeological junk — a clean reset
                                    beats trying to sanitize the old volume.
       ├── Google
       ├── Apple
       ├── iMessages
       ├── Anthropic
       └── OpenAI

(separate, disposable volume(s), ephemeral, deleted after verification)
  pdl_accept_<source>_pr<N>   <- created per acceptance run, torn down after
```

**Explicit architectural principle carried forward from the user:** do not
"salvage" the canonical corpus by merging old acceptance databases unless
raw-source reconstruction proves impossible. Re-importing from immutable raw
exports through the current importer code is cleaner than reconciling
historical intermediate schemas and PR-era bugs — PR #14's own body already
documents this exact reasoning for Apple's pre/post-v3-fix data.

**`personaldata` (the old v2–5 DB) will NOT be migrated or upgraded forward
into the canonical database, under any circumstance.** This is now a firm
decision, not a conditional one: extract whatever evidence it holds (if
anything its single Anthropic run contains isn't reproducible from the raw
Anthropic export via the current `import-anthropic-all`) into the repo as a
JSON summary, then build the canonical DB fresh at current schema on the new
dedicated volume regardless of what that evidence shows. Starting fresh is
categorically safer than mutating a stale schema-v2–5 database into
something later mistaken for having always been canonical.

### Proposed sequence

1. ~~**Disaster-recovery drill.**~~ **Done — see "Disaster-recovery restore
   drill" above.** All 6 backups proven restorable into a fresh, independent
   instance with exact field-for-field matches before any of the steps below
   begin. This was moved ahead of rebuild design per explicit instruction:
   verify the safety net before the biggest state transition in the project,
   not after.
2. **Preserve evidence, not databases.** Extract compact JSON summaries
   (counts, hashes, timestamps) from each acceptance DB into the repo before
   any cleanup, so "PR13 passed" remains provable without keeping a 2 GB
   database around to prove it.
3. **Extract `personaldata`'s evidence, then leave it frozen.** Per the firm
   decision above — this is no longer conditional.
4. ~~**Create `pdl_canonical_postgres_data`** as a new, dedicated Docker
   volume — never reuse `repo_postgres_data`.~~ **Done — see "Update 2026-09-19" near the top of
   this file.**
5. ~~**Rebuild `personaldata` from raw exports only**, via current importer
   code, one source at a time.~~ **Done — executed in the order Anthropic → OpenAI → iMessages →
   Apple → Google, not the Google-first order originally proposed here. Not by copying
   acceptance-DB rows.**
6. ~~**Idempotency check.**~~ **Done — see the "Update, same day" note above.** Re-run each importer
   a second time against the same raw exports; canonical counts/provenance must not change
   (this project's own reobservation/exact-replay guarantee, already proven
   per-source in each PR's acceptance report — this step just proves it
   holds for the *combined* canonical DB too).
7. **Point `.env`/`DATABASE_URL` at `personaldata`** as the real default.
8. **Only after all of the above passes:** delete the databases flagged
   "likely superseded" above (after row-level hash verification, not
   aggregate-count assumption) and reclaim the ~17+ GB in Docker volumes.

### Guardrails to build alongside the rebuild (not yet built)

Per your list — flagging these as future engineering work, not yet started,
roughly in the order they'd need to exist to make step 4 onward safe:

- **`pdl doctor`** — one command reporting repo version, DB target, schema
  version, connected volume, source folders found, backup status, orphaned
  acceptance DBs, suspicious config. Highest-value single piece per your
  own flag.
- **Canonical identity in code**, not just docs — one config key defines it;
  startup fails loudly if pointed at a `_test`/`_acceptance`/unknown DB in
  normal mode.
- **Environment guards** — acceptance commands require `PDL_ENV=acceptance`
  and refuse to target the canonical DB; canonical import commands refuse
  DBs matching test-naming patterns.
- **Run-level provenance** — commit SHA, importer version, source-file
  hashes, command/options, schema version, timestamp, DB target, captured
  per import run.
- **Architecture invariants as tests** — canonical DB cannot have `_test`
  suffix; acceptance DB creation must never target canonical; raw-source
  directories are read-only inputs; imports must be idempotent.
- **Retention policy** (proposed, not yet enforced): canonical backups kept
  indefinitely/monthly; acceptance DBs 0 days by default; failed acceptance
  DBs 7 days; raw exports permanent.
- **Size/row-count baselines** post-rebuild, to flag future 90% drops or 10x
  explosions on reimport.
- **Classification tag per artifact**: raw/irreplaceable,
  canonical/durable, derived/regenerable, ephemeral/test — drives backup and
  cleanup rules.

## Open questions still unresolved

- `projects-000.zip` location (manifest-declared, not sibling to the
  Anthropic manifest) — filing problem, not yet evidence the file is wrong.
- The Apple export folder holds two overlapping export batches
  (friendly-named + slug-named zips) — not yet reconciled.
- Whether the frozen `personaldata` Anthropic run has anything not
  reproducible via the current `import-anthropic-all`.
- The `iMesages` folder-name typo — correct only via a deliberate migration
  with code/docs updated together, not a silent rename.
- Row-level (not just aggregate-count) uniqueness diffing between the near-
  duplicate Apple and Google databases has not been performed.

## Update 2026-09-28: Phase 9 (topic clustering) real-rolled-out; Phase 10 (life graph) extraction in progress

Two derive-layer phases landed on top of the canonical `personaldata` database recorded above.
See [`PROJECT_STATE.md`](PROJECT_STATE.md) for full evidence on each; this section only tracks
what changed in the database itself.

- **Phase 9 (topic clustering)** — real-rolled-out and verified (PR #77, merged 2026-09-28):
  397 topic groups, 396 real clusters plus a noise bucket, exact 1:1 row count against every
  real `semantic.text_unit` in canonical. Nothing pending here.
- **Phase 10 (life graph)** — schema and deriver code fully implemented and tested (PR #77,
  same merge): migration 023 applied to canonical (`schema_version: 23`). The *real* extraction
  pass is running as of this writing, not yet complete: a remote GPU (RunPod, llama.cpp server,
  bounded-grammar decoding against every real `semantic.text_unit`) is fetching structured
  extractions for all ~70,145 real text units. As of the last check, roughly 32,500/58,633
  remaining units were fetched, a small (~0.15%) and expected error rate from Cloudflare
  proxy timeouts on the longest individual generations, ETA a few more hours.
  **The `generative.extraction`, `entity.mention`, `entity.relation_candidate`, `event.*`, and
  `intent.*` tables are still empty or partial in canonical right now** — the fetch results
  have not yet been applied via `derive.run`, and the `--global` resolver derivers
  (entity/event/intent resolution, claim evaluation, contradiction detection) have not yet run
  to settle the graph. Do not build against real life-graph row counts, entity counts, or
  relation-type distributions until this section is updated again with final numbers.

**Update 2026-09-29: Phase 10 real rollout complete.** Canonical is at schema 24 (migration 024
adds `generative.extraction.extraction_status`). Every active derive partition is clean.
`generative.extraction` holds 70,145 rows, one per text unit: 69,959 `complete` and 186 `truncated`
(model fields NULL, never defaults). Downstream: 358,031 `entity.mention`, 254,727
`entity.relation_candidate`, 32,495 `event.time_resolution`, 6,171 unconfirmed
`entity.link_candidate`, 1,189 `know.claim`, 1 `know.claim_contradiction`. `entity.resolved_mention`
is empty (only 3 `people` rows), so `event.cluster_candidate` and `intent.cluster_candidate` are
empty by design until the identity bootstrap. The active `generative_extract_text_unit` version is
33 (`code_version` 2); versions 24 and 32 are retired and never produced surviving rows. See
`PROJECT_STATE.md`'s "Phase 10: real rollout" for the evidence and the calibration caveat on
event/decision/open-loop labels.

**Update 2026-09-29 (later): identity bootstrap + UI v1.** Canonical is at schema 26.
Migration 025 adds `identity.alias` (per-person display aliases used by alias-aware mention
resolution). Migration 026 adds the `app` schema: `app.investigation` and
`app.investigation_revision` (saved UI answers with plan, full insight, evidence snapshot,
deriver versions). Current counts: 22 `people`, 52 `identity_links`, 36 `identity.alias`,
16,813 `entity.link_candidate` rows all still `candidate` (awaiting the owner's confirmation,
now possible in bulk from the app's People view), and therefore 0 `entity.person_link` and 0
`entity.resolved_mention`, so the event/intent cluster resolvers still have nothing to cluster.
`app.investigation` is empty (test investigations made during UI verification were deleted).

**Update 2026-09-29 (later still): owner confirmed person mentions.** 15,607 of the 16,813
exact-match candidates were bulk-confirmed into `entity.person_link` (so `entity.resolved_mention`
now has 15,607 rows). The 1,206 held back are the bare kinship words "mom"/"dad": text units
are conversation chunks with no per-message speaker, so "mom" written by someone else usually
means *their* mother, and those stay `candidate` for per-thread review. After rebuilding the
downstream resolvers (global run, 397 s, 4/4 partitions succeeded): 1,106 `entity.relation_edge`,
22 `event.cluster_candidate`, 2 `intent.cluster_candidate`; `know.claim` (1,189) and
`know.claim_contradiction` (1) unchanged.

**Update 2026-09-29 (disk cleanup): Docker now holds only the canonical instance.**
`personal-data-db` runs on `pdl_canonical_postgres_data` (canonical `personaldata` 13 GB,
`personaldata_test` about 100 MB). The only other volumes are the two anonymous `pdl_test` volumes
(do-not-delete item 5). 23 further anonymous volumes, left behind by throwaway Postgres containers
during the 2026-09-19..22 test runs, were removed after reading each one's database catalog
(names only `personaldata_*_test` and `pdl_doctor_fresh_test`). The old `repo_postgres_data` and
`pdl_pr13_postgres_data` were retired to verified dumps; see the do-not-delete list above.
`docker_data.vhdx` must be compacted (`Optimize-VHD`, admin) to return freed space to C:.

**Correction 2026-09-29: a fourth Postgres instance existed in WSL Ubuntu.** The 2026-09-19
inventory missed a native PostgreSQL 18 + pgvector cluster inside the `Ubuntu` WSL distro
(`/var/lib/postgresql/18/main`, port 5432, set up 2026-09-18 and not auto-started since Docker
took the port). It held `personaldata_apple_v3_acceptance_test`, `personaldata_apple_v3_corpus_test`
(3.8 GB), `personaldata_openai_acceptance_test` and `personaldata_openai_corpus_test` (267 MB,
matching PR #17's reported size). So the OpenAI acceptance import *was* retained locally,
contrary to "Did OpenAI ever get imported?" above. All four were dumped and verified into
`DataLabBackups\2026-09-29-retired-volumes\` (MANIFEST rows tagged `wsl-ubuntu`, plus
`globals_wsl_ubuntu.sql`); the owner may then remove the distro with `wsl --unregister Ubuntu`.

**Update 2026-09-30: seventh source `ios_backup`, schema 27.** The raw backup is retained (hard
links) at `DataLabData\iMazing\iPhone 13 Pro backup 2026-09-30\`; it belongs on the do-not-delete
list with the other raw exports. Canonical now holds 695 `ios_backup` audio assets, 152
`voice_memo` records (111 with on-device transcript text), 306 `voicemail` records (265 with Apple
transcript text), and 458 record-to-audio relationships, all resolving. Migration 027 adds
`media.audio_fingerprint` (1,178 ok, 11 unreadable) and `media.audio_supersession` (158 rows: 54
exact_duplicate, 37 same_audio, 67 contained_in; detector `contain-v2`). Superseded assets stay in
`assets` as provenance; Whisper skips them. The v1 voicemail records were deleted and re-imported
under parser v4 after a same-snapshot conflict (see `PROJECT_STATE.md`).

**Update 2026-09-30 (later): schema 28, iPhone transcripts.** `media.transcript` has 219 `ios_backup`
rows (214 succeeded, 101 with text; 5 unreadable), model `faster-whisper-large-v3-turbo-batched`.
Migration 028 adds `policy.source_policy` for `ios_backup` (`intimate`, `local_only`).
`semantic.text_unit_document` gained 376 `ios_backup` units (265 voicemails, 111 Voice Memos),
embedded. Still dirty: the LLM-backed extraction and annotation partitions for those units, and the
GLOBAL rollups. Whisper transcripts in `media.transcript` are not yet part of the semantic layer for
any source (see `PROJECT_STATE.md`).

**Update 2026-09-30 (evening): schema 29, audio in the semantic layer.** Migration 029 adds
`record_id`/`asset_id`/`chunk_index` to `semantic.text_unit` and `semantic.text_unit_document`
(document `record_id` now nullable, one-origin CHECK, per-origin chunk uniqueness). Canonical:
70,752 text units (607 new `ios_backup`, all extracted and topic-labeled), 3,810 document units
(v2 chunking plus Whisper transcripts), 2,983 new entity mentions, every active partition clean.

**Update 2026-09-30 (night): eighth source `ios_messages`, schema 30.** The phone's own Messages
database, holding only what the iMazing CSV source (`imessages`) lacks. Migration 030 adds its
`policy.source_policy` row (`intimate`, `local_only`); `declare.source_contract` covers 2020-08-28
to 2026-09-30. Canonical now holds, for `ios_messages`: 24,790 events (18,191 messages, 5,862
reactions + 11 removals, 175 group notices, 85 edits, 399 deletion events; 477 messages are
`provider_deleted`), 550 conversations (device chats, plus "Deleted chat with ..." orphan threads),
76,864 relationships (66,365 `csv_message_on_device` from CSV events to device chats, 746
`more_complete_copy_of`, reactions, replies, attachment links), 2,707 attachment assets (128 files
present, hashed), and 18 identity links mirrored from confirmed `imessages` identities. A device
message whose CSV twin has no text carries metadata `csv_twin`; every count skips those. `semantic.text_unit`
has 75,717 rows (4,965 new `ios_messages`, all extracted and topic-labeled), with 12,915 new entity
mentions. 1,616 new name-mention candidates (Ryan, Veyda, Sarah, Chris, Cody, Lyle, Maddie, Ashley, ...,
and bare "mom"/"dad") await confirmation. Raw inputs, never to delete: the backup folder and
`DataLabData\iMazing\chat.db snapshot 2026-04-03\chat.db`. Every active partition is clean; the
only dirty partitions belong to retired deriver versions.

**Update 2026-09-30 (night, later): `ios_messages` name mentions confirmed.** Ryan confirmed 1,418
of the 1,616 pending `ios_messages` name-mention candidates into `entity.person_link` (15 people;
active person links now 17,114). The 198 held back are the bare kinship words "mom" (130) and
"dad" (68), as with every earlier bulk confirmation. `event_resolve_candidates` and
`intent_resolve_candidates` were re-dirtied and re-run afterwards (they read the confirmed links
but are not invalidated by a confirmation); both succeeded. Every active partition is clean.

**Update 2026-10-02: iPhone Contacts and Notes (no schema change; still 30).** `ios_backup` now also holds
157 `note` records (1 locked, 4 with no body), 3 `call_recording` records (Apple's on-device call
transcripts, `transcript_source: apple_notes`), 1 `note_attachment` record, 4 `contact` records
(`import_reason: new`; 120 phone contacts were duplicates of Apple/Google cards and are not stored),
9 `ios_backup/notes_media` assets (plus the 6 `notes_audio` assets already present), and
`note_has_attachment` / `note_attachment_has_file` relationships. `semantic.text_unit` for `ios_backup`:
761 units (153 from notes, 4 from call/attachment text; 3 Whisper-derived units retired as redundant),
all extracted and topic-labeled. `media.transcript`: 717 rows (3 retired, see PROJECT_STATE).

**Update 2026-10-02 (later): iPhone Calendar.** 220 `calendar_event` events under `ios_backup` (namespace
`ios_backup/calendar/event`; 122 subscribed-calendar and 83 already-held events were not stored). Event time
is the phone's instant; an all-day event is its floating date at 00:00 UTC.
