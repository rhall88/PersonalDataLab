-- Phase 4 of the derived meaning architecture (DERIVED_MEANING_ARCHITECTURE.md, sections 6 and 15):
-- signals (baselines, change points, anomalies), interaction dynamics/states, and episodes
-- (declared/detected; derived is out of scope -- see PHASE4_IMPLEMENTATION_PLAN.md decision 8).
-- No canonical table (events/records/etc.) is altered by this migration.

CREATE SCHEMA IF NOT EXISTS signal;
CREATE SCHEMA IF NOT EXISTS episode;

-- Two of the architecture's six named baseline kinds (decision 2): a rolling window over the most
-- recent known observations, and an expanding lifetime window. Keyed off derive.metric_def so this
-- rides Phase 3's existing metric catalog rather than inventing a second one.
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

-- PELT (ruptures) change points per metric over its full known-value history (decision 3).
-- Recomputed in full on every derive run --global, so this is a clear-and-replace table per
-- metric, not an append-only log.
CREATE TABLE signal.change_point (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    metric_id BIGINT NOT NULL REFERENCES derive.metric_def(id) ON DELETE CASCADE,
    at_date DATE NOT NULL,
    method TEXT NOT NULL DEFAULT 'pelt',
    computed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (metric_id, at_date, method)
);

-- Robust z-score against the trailing_90d baseline, stored only where |z| >= 3.0 (decision 4) --
-- this table holds anomalies, not a row for every known day.
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

-- Per-(source, correspondent, month) interaction metrics, scoped to facebook + imessages 1:1
-- threads only (decision 9). Keyed by raw (source, correspondent) rather than a metric id --
-- correspondents are not metrics, so there is no derive.metric_def row to reuse for them.
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

-- PELT change-point segmentation of each correspondent's monthly reciprocity_index series
-- (decision 10). Each state is described purely by the mean of every interaction metric over its
-- span -- no LLM labeling.
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

-- Episodes: DECLARED (a person names a period; start/end are exact, so the range collapses to a
-- single point) and DETECTED (cross-source-family change-point clustering, decision 7; boundary is
-- genuinely uncertain, so the range is the cluster's min/max date). DERIVED is out of scope for
-- this phase (decision 8).
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

CREATE INDEX interaction_dynamics_bucket_idx ON measure.interaction_dynamics (bucket_start);
CREATE INDEX change_point_metric_idx ON signal.change_point (metric_id, at_date);
CREATE INDEX episode_kind_status_idx ON episode.episode (kind, status);
