# Phase 4 implementation plan: signals + episodes

`DERIVED_MEANING_ARCHITECTURE.md` section 15's roadmap, phase 4: "Baseline kinds,
autocorrelation-aware intervals, change points, anomalies, cycles, trips, project spans, declared
episodes, interaction states." Exit test: "Detected episodes reviewed against 10 periods you
know." This plan scopes that phase against what section 6 ("Temporal and behavioral analysis")
actually specifies, grounded in the real canonical database as it exists after Phases 0-3.

## Grounding (queried against the real database, 2026-09-22)

- `measure.metric_daily` (Phase 3) already holds 20 metrics with real per-day history, from as
  little as 317 known days (`ai_chat.total_turns_count`) to as many as 13,224
  (`google.mail_messages_count`, whose long span reflects imported historical mail, not daily
  activity every day). This is the only real time series available to compute baselines, change
  points and anomalies against -- there is no other numeric daily signal in the corpus.
- Identity resolution (Phase 2) has, in practice, only ever cross-source-linked the account
  owner's own identity (`identity_links` has exactly 2 active rows, both `person_id = 1`,
  "Ryan Hallgarth" -- one for facebook, one for google; the two rules-tier candidates from Phase 2's
  rollout are still queued for review and represent no confirmed *other-person* cross-source link
  today). Every other correspondent exists only as a raw `(source, actor_identity)` pair.
- `google`'s `actor_identity` values on `mail_message` are dominated by bulk/automated senders
  (USAA, Shoe Carnival, Newegg, LinkedIn digests, "Fing Alert", ...), and google's
  `events.conversation_id` is NULL on every single row (confirmed: 0 of 164,063 rows). This is a
  real finding, not a design choice: google has no usable "conversation" or "correspondent" signal
  for interaction dynamics. It also puts a real caveat on Phase 3's
  `communication.unique_correspondents_count`, which does count google's raw actor_identity values
  -- that metric's real-world number is inflated by mailing-list and automated-sender noise for the
  google contribution specifically. Not retroactively changed here (Phase 3 is already reviewed and
  merging); flagged in `PROJECT_STATE.md` for Ry's awareness.
- `facebook`'s `message` events use the sender's display name as `actor_identity` ("Ryan
  Hallgarth" for outgoing, the correspondent's name for incoming; "Facebook user" for a
  deleted/unknown participant -- already in Phase 2's `identity_normalize.ROLE_LABELS`).
  306 of 470 facebook conversations with any actor identity have exactly one non-owner correspondent
  (a clean 1:1 thread); the rest are group threads or have no resolvable participant.
- `imessages` only stamps `actor_identity` on `message.incoming` (the correspondent's phone
  number or, rarely, an email); `message.outgoing` always carries the literal string `"unknown"`,
  never a real identity. 25 of imessages' conversations resolve to exactly one distinct incoming
  `actor_identity` (a clean 1:1 thread); the majority are group threads.
- `numpy`, `scipy` and `ruptures` are not installed. `ruptures` (the architecture's own named PELT
  implementation, section 6's method table) pulls in `numpy`/`scipy` as its only two transitive
  dependencies -- installs cleanly, no network extension risk beyond what Phase 3 already accepted
  for `duckdb`.

## Corrections to prior claims

- Phase 3's `communication.unique_correspondents_count` is real and matches its own definition
  exactly (confirmed by its own spot checks), but that definition's real-world meaning is weaker
  for the google contribution than the metric name suggests, per the grounding above. No code
  change; documented as a known limitation.

## Decisions

1. **Scope: signals, plus a scoped slice of episodes and interaction dynamics. Communication +
   AI-chat domain only, continuing Phase 3's own scope decision.** No location, git, browsing or
   calendar-scale source exists in this corpus (Phase 3's own grounding, unchanged), so DERIVED
   episodes that need them (trips, project spans, jobs) are out of scope. The discovery gate
   (section 6) is explicitly phase 10's own roadmap item, not touched here.
2. **Baselines: two of the architecture's six named kinds.** `trailing_90d` (median and MAD over
   the most recent 90 *known* observations -- a rolling window over available days, not calendar
   days, since coverage has real gaps and a calendar window would silently treat a gap as absent
   evidence rather than what it is) and `lifetime` (median and MAD over all known days up to and
   including the day), each gated on a minimum of 14 (trailing) or 5 (lifetime) prior known
   observations -- below that, no baseline row, not a degenerate near-zero-spread one. Same-weekday,
   same-months-prior-year, preceding-matched-window and pre-event-window baselines need seasonal or
   event-boundary machinery that belongs with phase 6's actual before/after templates, not here.
3. **Change points: PELT via `ruptures`**, exactly the method the architecture names. Runs per
   metric over the full ordered sequence of *known* values (gaps are simply absent from the
   sequence, never a fabricated zero), GLOBAL class, recomputed in full on every `derive run
   --global` (clear-and-replace, like Phase 3's global deriver). `ruptures>=1.1,<2` added as a
   dependency (pulls in `numpy`, `scipy`). Bayesian online change point (the streaming variant for
   new data) is deferred: nothing in this phase consumes changes incrementally yet.
4. **Anomalies: robust z-score against the `trailing_90d` baseline** (the more locally-relevant of
   the two per baselines' own precedence -- trailing window before lifetime), computed only on
   known days, stored only where `|z| >= 3.0`. Each row: observed value, expected range (`center +-
   3.0*spread`), baseline kind, z-score, coverage (implicitly 1.0 -- gated on known), method.
5. **Trend, cycles, before/after, stability, association: deferred.** None are required by phase
   4's own exit test (detected episodes vs. known periods), and none of them are actually
   checkable without a real before/after template or a declared closeness/seasonality target to
   validate against -- building them now would be untested scope creep.
6. **Declared episodes**: a plain table plus an `episode declare` CLI command. `start`/`end` are
   given as exact dates (a single-point range, i.e. `start_range_low = start_range_high`), matching
   "you name a period" -- the uncertain-boundary model exists for derived/detected episodes, not for
   a person's own explicit statement.
7. **Detected episodes**: cluster change-point dates across *independent* metrics -- independence
   taken at the source-family level (facebook / imessages / google / anthropic / openai are already
   treated as independent provenance families by Phase 1's design). A candidate episode is any
   window where change points from >= 2 different source families fall within 21 days of each
   other; its boundary range is the min/max date in the cluster, its evidence is the list of
   contributing `(metric, change_point_date)` pairs, `status = 'candidate'`, no label. No LLM
   labeling step -- the architecture is explicit that a suggested label is a HYPOTHESIS, a later
   layer this phase doesn't build.
8. **Derived episodes: out of scope** (trips, project spans, jobs -- no location, git or payroll
   source exists in this corpus; same grounding as decision 1).
9. **Interaction dynamics: facebook + imessages only, 1:1 threads only.** Google is excluded per
   the grounding above (no conversation signal, correspondent identity dominated by automated
   senders). anthropic/openai are excluded (a chat with an assistant is not a person). A thread
   qualifies only when it resolves to exactly one non-owner correspondent (306 facebook, 25
   imessages threads) -- a per-pair "who initiated / how reciprocal" model has no well-defined
   meaning for a 3+-party group thread, and this project has not built (and section 6 does not
   specify) a multi-party attribution model. Computed metrics per `(source, correspondent,
   month)`: `messages_in`, `messages_out`, `reciprocity_index` (`min(in,out)/max(in,out)`, 0..1),
   `initiation_share` (share of "new exchanges" -- a message following a >=4h gap from the prior
   one in that thread -- sent by the account owner), `median_response_latency_seconds` (median gap
   between consecutive messages that switch sender, either direction), `n_conversations`. Channel
   mix, active hours, co-location, shared calendar events and photo co-occurrence are dropped:
   correspondents are not resolved across sources in this corpus (decision context above), so
   "channel mix" for a single-source correspondent is vacuous, and there is no location/calendar/
   photo source at all (same grounding as decision 1/8).
10. **Interaction states**: PELT (the same mechanism as decision 3) applied to each correspondent's
    monthly `reciprocity_index` series -- the one interaction metric best suited to describe a
    relationship's character over time. Each resulting state stores the mean of every interaction
    metric over its span, "described in metric terms" per the architecture, with no LLM labeling.
11. **CLI**: `personal-data-lab episode declare --start YYYY-MM-DD --end YYYY-MM-DD --label ...
    [--note ...]` and `personal-data-lab episode list [--kind declared|derived|detected]`. No new
    browsing CLI for signals themselves in this phase -- they are tables a later template/cockpit
    reads directly, matching how `measure.metric_daily` shipped in Phase 3 without its own read
    CLI beyond `compare`.

## Schema (migration 014)

```sql
CREATE SCHEMA IF NOT EXISTS signal;
CREATE SCHEMA IF NOT EXISTS episode;

CREATE TABLE signal.baseline (
    metric_id BIGINT NOT NULL REFERENCES derive.metric_def(id) ON DELETE CASCADE,
    bucket_start DATE NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('trailing_90d', 'lifetime')),
    center DOUBLE PRECISION NOT NULL,
    spread DOUBLE PRECISION NOT NULL,
    n_obs INTEGER NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (metric_id, bucket_start, kind)
);

CREATE TABLE signal.change_point (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    metric_id BIGINT NOT NULL REFERENCES derive.metric_def(id) ON DELETE CASCADE,
    at_date DATE NOT NULL,
    method TEXT NOT NULL DEFAULT 'pelt',
    computed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (metric_id, at_date, method)
);

CREATE TABLE signal.anomaly (
    metric_id BIGINT NOT NULL REFERENCES derive.metric_def(id) ON DELETE CASCADE,
    bucket_start DATE NOT NULL,
    observed DOUBLE PRECISION NOT NULL,
    baseline_kind TEXT NOT NULL,
    expected_low DOUBLE PRECISION NOT NULL,
    expected_high DOUBLE PRECISION NOT NULL,
    z_score DOUBLE PRECISION NOT NULL,
    method TEXT NOT NULL DEFAULT 'robust_z',
    computed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (metric_id, bucket_start)
);

CREATE TABLE measure.interaction_dynamics (
    source TEXT NOT NULL,
    correspondent TEXT NOT NULL,
    bucket_start DATE NOT NULL,
    messages_in INTEGER NOT NULL,
    messages_out INTEGER NOT NULL,
    n_conversations INTEGER NOT NULL,
    reciprocity_index DOUBLE PRECISION NOT NULL,
    initiation_share DOUBLE PRECISION,
    median_response_latency_seconds DOUBLE PRECISION,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source, correspondent, bucket_start)
);

CREATE TABLE signal.interaction_state (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    correspondent TEXT NOT NULL,
    state_start DATE NOT NULL,
    state_end DATE NOT NULL,
    mean_messages_in DOUBLE PRECISION NOT NULL,
    mean_messages_out DOUBLE PRECISION NOT NULL,
    mean_reciprocity_index DOUBLE PRECISION NOT NULL,
    mean_initiation_share DOUBLE PRECISION,
    mean_median_response_latency_seconds DOUBLE PRECISION,
    n_months INTEGER NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source, correspondent, state_start)
);

CREATE TABLE episode.episode (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('declared', 'derived', 'detected')),
    status TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'confirmed', 'rejected')),
    start_range_low DATE NOT NULL,
    start_range_high DATE NOT NULL,
    end_range_low DATE NOT NULL,
    end_range_high DATE NOT NULL,
    label TEXT,
    note TEXT,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

`signal.baseline`/`signal.change_point`/`signal.anomaly` key off `derive.metric_def.id`
(Phase 3), so they ride the exact same metric catalog without inventing a parallel one.
`measure.interaction_dynamics` and `signal.interaction_state` are keyed by raw `(source,
correspondent)` rather than a metric id, since correspondents are not metrics -- there is no
existing catalog row to reuse for them.

## Runtime

- `derive/derivers/signals.py`: `signal_baseline` (GLOBAL, `inputs=("measure_metric_daily",
  "measure_metric_daily_global")`, clear-and-replace per metric) computing `trailing_90d` and
  `lifetime` baselines; `signal_change_point` (GLOBAL, same inputs, clear-and-replace per metric)
  running `ruptures.Pelt` per metric's known-value sequence; `signal_anomaly` (GLOBAL, `inputs=
  ("signal_baseline",)`, clear-and-replace per metric) computing robust z against `trailing_90d`
  and keeping only `|z| >= 3.0` rows.
- `derive/derivers/interaction.py`: `measure_interaction_dynamics` (GLOBAL, `inputs=
  ("measure_coverage",)`, clear-and-replace per `(source, correspondent)`) computing the 1:1-thread
  metrics from decision 9 directly from `events`; `signal_interaction_state` (GLOBAL, `inputs=
  ("measure_interaction_dynamics",)`, clear-and-replace per `(source, correspondent)`) running PELT
  on each correspondent's `reciprocity_index` series.
- `episode.py`: `declare_episode(engine, start, end, label, note)`; `list_episodes(engine, kind=None)`;
  `detect_episodes(connection)` (decision 7's clustering, called from the `signal_change_point`
  deriver's own compute step once change points are written, since it needs every metric's change
  points in hand -- not a separate deriver, to avoid inventing a dependency edge back onto a
  GLOBAL deriver that already ran this cycle).
- `cli.py`: `episode declare` / `episode list` subcommands.
- `pyproject.toml`: add `ruptures>=1.1,<2`.

## Exit tests

1. `sync` registers the 3 new GLOBAL signal derivers and the 2 new interaction derivers into
   `derive.deriver` alongside Phase 0-3's existing ones.
2. A synthetic metric history with a known, deliberate level shift produces a `signal.change_point`
   row within a few days of the true shift, and `signal.baseline`/`signal.anomaly` rows match a
   hand-computed median/MAD/z-score for a chosen day.
3. A day with fewer than the minimum required known prior observations produces no baseline row
   (never a degenerate one) -- mirrors Phase 3's "unknown never renders as 0" exit test.
4. A synthetic 1:1 thread with a known message pattern produces exactly the hand-counted
   `messages_in`/`messages_out`/`reciprocity_index`/`initiation_share`; a 3-party thread produces no
   `measure.interaction_dynamics` row at all.
5. `episode declare` round-trips through `episode list`; a synthetic pair of correlated change
   points across two source families produces one `episode.episode` row of kind `detected` whose
   evidence cites both.
6. Real-database rollout: change points and anomalies reviewed by hand against at least 10 stretches
   of real history Ry can recognize (large real events already visible in the metric history --
   the Phase 4 exit test's own bar), plus a spot check of `signal.baseline`/`signal.anomaly` values
   against an independently computed rolling median/MAD for a few real metrics and days.
