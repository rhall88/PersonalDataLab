# Phase 3 implementation plan: the life ledger

Phase 3 of `DERIVED_MEANING_ARCHITECTURE.md` (S15): "`metric_def` with observation contracts,
daily metrics (communication, location stays, git, browsing, calendar, AI chats), DuckDB wiring."
Usable result: `pdl compare --focal ... --baseline ...`, a ranked change table. Exit test: 20
metrics spot-checked against raw records; unknown never renders as 0.

This plan follows the same cadence as Phases 0-2: read the real data and the existing runtime
before designing, correct the architecture doc's own assumptions where the real data disagrees,
and reuse Phase 0's derivation machinery (registry, versioning, change feed, SKIP LOCKED worker,
partition classes) rather than inventing a second one.

## Grounding in the real data

Queried directly against the real canonical database (`events` grouped by `source, event_type`,
and `declare.source_contract`/`measure.coverage` from Phase 1):

- The six ingested source families are Google Takeout, Apple, iMessages, OpenAI, Anthropic, and
  Facebook. There is no calendar, git, browsing, or location-stay source anywhere in this corpus
  (`apple`'s 34 real `event_type` values are exclusively Apple Music/App Store/notification/
  purchase activity plus a handful of `calendar_event`/`reminder` rows -- no communication and no
  location-stay signal at all).
- Real communication-shaped event types, by source: `facebook` -- `message` (50,574), `call`
  (67); `imessages` -- `message.incoming` (35,965), `message.outgoing` (31,915),
  `message.reaction` (3,486), `message.edit` (364); `google` -- `mail_message` (47,277),
  `chat_message` (403), `voice_communication` (127), `meet_call` (5); `anthropic` and `openai` --
  `conversation_message` (9,673 / 25,510), the "AI chats" domain the architecture lists
  separately.
- `events.conversation_id` exists on every event row (confirmed from `information_schema`), so
  "active conversations that day" is a plain `count(distinct conversation_id)` for every one of
  these sources -- no new column needed.
- Phase 1's `measure.coverage` has exactly one row per `(source, bucket_start, object_kind)` for
  every day inside that source's declared `covered_range`, and **no row at all** outside it
  (confirmed directly: `expected` is never NULL in the real table -- a declared day always gets a
  row, an undeclared or out-of-range day never does). Row *existence*, not any column inside the
  row, is Phase 1's actual "known vs. unknown" signal for a day. Phase 3 reuses this exactly: a
  metric's day is computed only when every `(source, object_kind)` it depends on already has a
  `measure.coverage` row for that day.

**Correction to the architecture doc's phase 3 line.** It lists six metric domains (communication,
location stays, git, browsing, calendar, AI chats) as if they ship together. Four of those six have
zero real observations in this database. Per architecture S15's own note ("First milestone: phases
0-3 on communication data only... the richest cross-source domain, it stresses identity hardest"),
and because there is nothing to derive metrics *from* for the other four, this phase builds
communication + AI-chat metrics only. Location stays, git, and browsing metric definitions are
deferred until a source that actually produces that data is ingested; calendar events exist (53
Apple, 54 Google) but at a volume too thin to support a meaningful daily metric and are deferred
with them. This is the same kind of scope correction Phase 2 made when it found no contact-card
import to build a hard-rules tier from -- documented rather than silently narrowed.

## Decisions

1. **Scope to communication + AI-chat metrics, all six live sources.** See above. 17 per-source
   metrics plus 3 cross-source composites -- see the catalog below -- satisfy the "20 metrics"
   exit test entirely from data that is actually in the database.
2. **Metrics ride Phase 0's existing deriver framework unchanged.** A per-source metric deriver is
   `partition_class="LOCAL"` exactly like `measure_coverage`, registered with
   `inputs=("measure_coverage",)` so the registry's dependency ordering guarantees coverage for a
   partition is fresh before metrics read it. The three composites are one
   `partition_class="GLOBAL"` deriver (`inputs=("measure_metric_daily",)`), run only by
   `derive run --global` -- the same pattern `scheduler.py` already documents and the same
   worker, versioning, and fingerprint-skip machinery, no new invalidation logic.
3. **`metric_def` is a code-declared catalog, synced like `derive.deriver`.** `DeriverRegistry`
   already treats deriver shape as code, not schema, and upserts it into `derive.deriver` on
   every `derive sync` (migration 010's own comment on this). `metric_def` and its per-metric
   `(source, object_kind)` dependency set are exactly as code-shaped, so they get the identical
   treatment: a static `METRIC_CATALOG` tuple in `derive/derivers/metrics.py`, upserted into the
   new `derive.metric_def` / `derive.metric_dependency` tables by `derive sync`. Nothing about a
   metric's definition is hand-seeded in the migration.
4. **A metric's day is a real row or no row -- never a fabricated zero.** `measure.metric_daily`
   copies `measure.coverage`'s own convention (see migration 011's comment) instead of inventing
   a second one: no `expected`/`unknown` sentinel column, just PK `(metric_id, bucket_start)` and
   a NOT NULL `value`. A day with zero genuine activity is a real row with `value = 0`; a day
   Phase 1 hasn't attested as covered gets no row at all, which is what "unknown never renders as
   0" means operationally in this schema.
5. **A composite metric's day is known only when every metric it sums is known that day.** If
   `imessages` is covered on a day but `google` is not, `messaging.total_messages_count` for that
   day is *not* silently computed from `imessages` alone (which would understate the true total
   and look like a real, low number) -- it gets no row, same as any other unknown day. Conservative
   by the same reasoning as Decision 4.
6. **Sent/received direction is out of scope.** Facebook `actor_identity` is a bare display name
   with no self/other flag (confirmed in Phase 2's grounding); splitting "messages I sent" from
   "messages I received" needs a reliable per-source account-owner identity, which Phase 2 left as
   a real, unresolved gap (`identity-candidates --cross-source` review queue, not yet acted on).
   Phase 3 metrics count all communication activity per day regardless of direction. Direction
   splits are a later phase once an account-owner identity is resolved per source.
7. **DuckDB queries rows fetched from Postgres, not DuckDB's own postgres scanner extension.**
   The `postgres_scanner` extension needs a first-run network download; this project otherwise has
   zero non-Postgres runtime dependencies and no other place assumes outbound network access at
   query time. `compare` instead pulls the requested `measure.metric_daily` rows via the existing
   SQLAlchemy engine into memory and hands them to an in-memory DuckDB connection, which then does
   the actual aggregation/ranking in SQL. This is genuine "DuckDB wiring" -- DuckDB performs the
   analytical computation -- without a network dependency or a second copy of the data as a
   source of truth; Postgres's `derive`/`measure` schemas remain canonical.
8. **The CLI command is `personal-data-lab compare`, not `pdl compare`.** The architecture doc's
   `pdl` is illustrative shorthand; this project has one real console script,
   `personal-data-lab` (`pyproject.toml`), and this phase keeps using it rather than adding a
   second binary name for one command.
9. **`duckdb` becomes a required dependency**, not optional. `compare` is Phase 3's one
   user-facing deliverable ("the first real analytical capability arrives at phase 3" -- S15);
   making its library optional would make the phase's actual result opt-in.

## Metric catalog (17 per-source + 3 composite = 20)

Per-source (dependency is exactly the one `(source, object_kind)` pair named):

| Metric | Source | Object kind | Definition |
| --- | --- | --- | --- |
| `facebook.messages_count` | facebook | event | count of `message` events that day |
| `facebook.calls_count` | facebook | event | count of `call` events that day |
| `facebook.active_conversations_count` | facebook | event | `count(distinct conversation_id)` over `message`/`call` events that day |
| `imessages.incoming_count` | imessages | event | count of `message.incoming` events |
| `imessages.outgoing_count` | imessages | event | count of `message.outgoing` events |
| `imessages.reactions_count` | imessages | event | count of `message.reaction` events |
| `imessages.edits_count` | imessages | event | count of `message.edit` events |
| `imessages.active_conversations_count` | imessages | event | `count(distinct conversation_id)` over all `message.*` events |
| `google.mail_messages_count` | google | event | count of `mail_message` events |
| `google.chat_messages_count` | google | event | count of `chat_message` events |
| `google.voice_communications_count` | google | event | count of `voice_communication` events |
| `google.meet_calls_count` | google | event | count of `meet_call` events |
| `google.active_conversations_count` | google | event | `count(distinct conversation_id)` over `mail_message`/`chat_message` events |
| `anthropic.chat_turns_count` | anthropic | event | count of `conversation_message` events |
| `anthropic.active_conversations_count` | anthropic | event | `count(distinct conversation_id)` over `conversation_message` events |
| `openai.chat_turns_count` | openai | event | count of `conversation_message` events |
| `openai.active_conversations_count` | openai | event | `count(distinct conversation_id)` over `conversation_message` events |

Composite (GLOBAL, depends on every `(source, event)` pair its constituents use):

| Metric | Definition |
| --- | --- |
| `messaging.total_messages_count` | `facebook.messages_count + imessages.incoming_count + imessages.outgoing_count + google.mail_messages_count + google.chat_messages_count` |
| `ai_chat.total_turns_count` | `anthropic.chat_turns_count + openai.chat_turns_count` |
| `communication.unique_correspondents_count` | `count(distinct actor_identity)` across `facebook`/`imessages`/`google` communication events that day, excluding the known role-label/account-owner sentinels Phase 2's `identity_normalize.classify()` already recognizes (`ROLE_LABELS` plus `account_owner`) |

`communication.unique_correspondents_count` reuses Phase 2's `identity_normalize` module directly
(`classify(value).shape != "role_label"`) rather than re-deriving its own exclusion list --
one classifier, not two that can drift apart. This creates a real (not merely conceptual)
dependency on Phase 2's runtime module; Phase 3 branches from `main`, not from an unmerged Phase
2 branch, since `identity_normalize.py` is pure-Python with no schema dependency and can be
vendored/imported once Phase 2 merges. If Phase 2 has not merged by the time Phase 3 ships, this
one metric's exclusion list is inlined locally and reconciled in a follow-up, noted at rollout
time -- not a reason to block the other 19 metrics.

## Schema (migration 013)

```sql
CREATE TABLE derive.metric_def (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    unit TEXT NOT NULL,
    code_version TEXT NOT NULL DEFAULT '1',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE derive.metric_dependency (
    metric_id BIGINT NOT NULL REFERENCES derive.metric_def(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    object_kind TEXT NOT NULL,
    PRIMARY KEY (metric_id, source, object_kind)
);

CREATE TABLE measure.metric_daily (
    metric_id BIGINT NOT NULL REFERENCES derive.metric_def(id) ON DELETE CASCADE,
    bucket_start DATE NOT NULL,
    value NUMERIC NOT NULL,
    computed_epoch_id BIGINT NOT NULL REFERENCES derive.evidence_epoch(id),
    PRIMARY KEY (metric_id, bucket_start)
);
```

No canonical table is altered. `metric_def`/`metric_dependency` are seeded by `derive sync`
(Decision 3), not by this migration.

## Runtime

- `src/personal_data_lab/derive/derivers/metrics.py`: `METRIC_CATALOG` (the 17 per-source specs)
  and `COMPOSITE_CATALOG` (the 3 composites); `sync_metric_catalog(connection)` upserting both
  into `derive.metric_def`/`derive.metric_dependency`, called from the `derive sync` CLI handler
  alongside `REGISTRY.sync()`; the `measure_metric_daily` LOCAL deriver (loops over
  `METRIC_CATALOG` entries matching the partition's source, gates each day on a `measure.coverage`
  row for that `(source, day, object_kind)`, upserts `measure.metric_daily`); the
  `measure_metric_daily_global` GLOBAL deriver for the 3 composites.
- `src/personal_data_lab/compare.py`: `compare_periods(engine, focal, baseline, metrics=None)` --
  fetches `measure.metric_daily` rows for both periods, loads them into an in-memory `duckdb`
  connection, and runs one SQL query per period computing `sum(value)`, `count(*)` (known days),
  and the period's total calendar days, then joins focal against baseline for absolute and percent
  change, ranked by absolute change descending. Returns per metric: `focal_value`,
  `baseline_value`, `absolute_change`, `percent_change`, `focal_known_days`/`focal_total_days`,
  `baseline_known_days`/`baseline_total_days` -- so a period with poor coverage is visibly
  different from one with genuinely low activity, never silently collapsed into the same number.
- `cli.py`: `compare --focal START:END --baseline START:END [--metric NAME ...]`.

## Exit tests

1. `derive sync` upserts exactly 20 `derive.metric_def` rows with their declared dependencies.
2. Each of the 17 per-source metrics, run against a small synthetic fixture with a declared
   source contract, matches a hand-counted expectation from the fixture's raw events.
3. A day outside the fixture's declared `covered_range` produces no `measure.metric_daily` row
   for any metric depending on that source -- never a `value = 0` row.
4. The 3 composite metrics match the sum of their constituents on days where every constituent is
   known, and produce no row on a day where at least one constituent is unknown, even when the
   others are known.
5. `compare` between two synthetic periods ranks metrics by absolute change correctly and reports
   accurate known/total day counts for a period with a deliberately introduced coverage gap.
6. Real rollout: apply migration 13; run `derive sync` + `derive run` (LOCAL) + `derive run
   --global` against the real canonical database; spot-check at least 20 real
   `measure.metric_daily` values (covering every metric name at least once) against an
   independently written `count(*)`/`count(distinct ...)` query over the same raw events for the
   same day; run `compare` over two real, non-overlapping months and sanity-check the ranked
   output against what's already known about those months from earlier phases' spot-checks.
