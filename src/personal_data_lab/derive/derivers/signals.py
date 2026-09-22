"""Phase 4's signals (PHASE4_IMPLEMENTATION_PLAN.md): baselines, change points, and anomalies
over Phase 3's measure.metric_daily. All three ride Phase 0's deriver framework as GLOBAL
derivers -- no new invalidation logic -- and all three read only from measure.metric_daily
directly, never from each other's output tables.

That "never from each other's output" rule is deliberate, not incidental: the worker claims dirty
partitions in `dirty_since` order (worker.py's `_claim_one`), not topological dependency order,
and derive.scheduler's propagation can dirty two GLOBAL sibling derivers inside the same
transaction (where Postgres's CURRENT_TIMESTAMP is constant for the whole transaction), so two
GLOBAL derivers dirtied together have no guaranteed relative run order within one `derive run
--global` invocation. Phase 3's only GLOBAL deriver never hit this because there was only one.
signal_anomaly needs a baseline and could have declared inputs=("signal_baseline",) and read
signal.baseline directly, but that would only be correct if signal_baseline always ran first --
which the framework does not actually guarantee. Recomputing the trailing_90d baseline inline
(via the shared _trailing_90d_baselines() helper, imported by signal_baseline too) costs an extra
pass over data that is already in memory, and buys correctness that does not depend on execution
order. Documented as a real finding from this phase's own implementation, the same way Phase 1
and Phase 3 documented their own scheduling gaps in PROJECT_STATE.md.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date

import numpy as np
import ruptures
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ...fingerprints import fingerprint_value
from ..registry import ComputeResult, deriver

# Decision 2: gate baselines on a minimum number of known prior observations -- below this, no
# baseline row, never a degenerate near-zero-spread one.
_MIN_TRAILING_OBS = 14
_MIN_LIFETIME_OBS = 5
_TRAILING_WINDOW = 90

# Decision 4: an anomaly is stored only where the robust z-score against trailing_90d reaches
# this magnitude.
_ANOMALY_Z_THRESHOLD = 3.0

# PELT (decision 3): jump=5/min_size=10 keep the cost search tractable on the longest real series
# (~13,200 known days for some google metrics) without materially changing which shifts are
# found -- a shift shorter than 10 observations is not the kind of level change this phase is
# looking for. pen is a BIC-style penalty (sigma^2 * log(n)) scaled by a constant chosen to be
# conservative given real coverage gaps in the series; there is no principled "correct" value,
# and this is exactly what the exit test (change points reviewed against known real periods)
# validates.
_PELT_PENALTY_SCALE = 6.0


@dataclass(frozen=True)
class KnownSeries:
    metric_id: int
    days: list[date]
    values: list[float]


def _center_spread(values: list[float]) -> tuple[float, float]:
    """Median center, MAD-based spread scaled by 1.4826 (the standard MAD-to-sigma conversion
    for a normal distribution), so it is comparable to a standard deviation.
    """
    median = statistics.median(values)
    mad = statistics.median([abs(v - median) for v in values])
    return median, mad * 1.4826


def _all_known_series(connection: Connection) -> list[KnownSeries]:
    rows = connection.execute(
        text(
            "SELECT metric_id, bucket_start, value FROM measure.metric_daily "
            "ORDER BY metric_id, bucket_start"
        )
    ).all()
    by_metric: dict[int, KnownSeries] = {}
    for row in rows:
        series = by_metric.setdefault(row.metric_id, KnownSeries(row.metric_id, [], []))
        series.days.append(row.bucket_start)
        series.values.append(float(row.value))
    return list(by_metric.values())


def _trailing_90d_baselines(series: KnownSeries) -> list[tuple[date, float, float, int]]:
    """(day, center, spread, n_obs) for every day with >= _MIN_TRAILING_OBS known prior
    observations, using the most recent up to _TRAILING_WINDOW known observations -- a rolling
    window over available days, not calendar days (decision 2: coverage has real gaps, and a
    calendar window would silently treat a gap as absent evidence rather than what it is).
    """
    out: list[tuple[date, float, float, int]] = []
    for i in range(_MIN_TRAILING_OBS, len(series.values)):
        window = series.values[max(0, i - _TRAILING_WINDOW) : i]
        center, spread = _center_spread(window)
        out.append((series.days[i], center, spread, len(window)))
    return out


def _lifetime_baseline(series: KnownSeries) -> tuple[date, float, float, int] | None:
    """A single snapshot -- median/MAD over the metric's entire known history, stored against its
    most recent known day -- not one row per historical day. Nothing in this phase's own anomaly
    detection consumes a historical lifetime snapshot (decision 4 uses trailing_90d only); a
    per-day expanding-window lifetime baseline would cost O(n^2) for the ~13,200-day series with
    no consumer to justify it. Documented as a deliberate simplification of decision 2's "lifetime
    distribution" baseline kind, addressed here rather than in the plan doc since it only became
    apparent once real series lengths were in hand.
    """
    if len(series.values) < _MIN_LIFETIME_OBS:
        return None
    center, spread = _center_spread(series.values)
    return series.days[-1], center, spread, len(series.values)


def _change_points(series: KnownSeries) -> list[date]:
    """PELT change points over the known-value sequence (gaps are simply absent, never a
    fabricated zero). Normalizes by the series' own lifetime median/MAD first so one fixed penalty
    is meaningful across metrics of very different scale; a metric with no spread at all (MAD and
    stdev both zero -- e.g. a count metric that is almost always zero) has no meaningful change
    points to find and is skipped rather than dividing by zero.
    """
    n = len(series.values)
    if n < 2 * _MIN_TRAILING_OBS:
        return []
    median, mad = _center_spread(series.values)
    scale = mad if mad > 0 else statistics.pstdev(series.values)
    if scale == 0:
        return []
    normalized = np.array([(v - median) / scale for v in series.values])
    penalty = _PELT_PENALTY_SCALE * np.log(n)
    algo = ruptures.Pelt(model="l2", jump=5, min_size=10).fit(normalized)
    breakpoints = algo.predict(pen=penalty)
    # ruptures returns end-of-segment indices, the last of which is always n (not a real
    # breakpoint, just the series length) -- drop it.
    return [series.days[i] for i in breakpoints if i < n]


def _clear_all(table: str):
    def _clear(connection: Connection, partition_key: str) -> None:
        connection.execute(text(f"DELETE FROM {table}"))

    return _clear


@deriver(
    name="signal_baseline",
    partition_class="GLOBAL",
    final_inference="COMPUTED",
    description="Trailing-90-known-day and lifetime median/MAD baselines per metric (PHASE4_IMPLEMENTATION_PLAN.md).",
    output_relation="signal.baseline",
    inputs=("measure_metric_daily", "measure_metric_daily_global"),
    clear_partition=_clear_all("signal.baseline"),
)
def signal_baseline(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    all_series = _all_known_series(connection)
    output_rows: list[dict] = []
    fingerprint_parts: list = []
    for series in all_series:
        fingerprint_parts.append([series.metric_id, len(series.values), series.values[-5:]])
        for day, center, spread, n_obs in _trailing_90d_baselines(series):
            output_rows.append(
                {
                    "metric_id": series.metric_id,
                    "bucket_start": day,
                    "kind": "trailing_90d",
                    "center": center,
                    "spread": spread,
                    "n_obs": n_obs,
                }
            )
        lifetime = _lifetime_baseline(series)
        if lifetime is not None:
            day, center, spread, n_obs = lifetime
            output_rows.append(
                {
                    "metric_id": series.metric_id,
                    "bucket_start": day,
                    "kind": "lifetime",
                    "center": center,
                    "spread": spread,
                    "n_obs": n_obs,
                }
            )
    return ComputeResult(input_fingerprint=fingerprint_value(fingerprint_parts), rows=output_rows)


@deriver(
    name="signal_change_point",
    partition_class="GLOBAL",
    final_inference="COMPUTED",
    description="PELT change points per metric over its known-value history (PHASE4_IMPLEMENTATION_PLAN.md).",
    output_relation="signal.change_point",
    inputs=("measure_metric_daily", "measure_metric_daily_global"),
    clear_partition=_clear_all("signal.change_point"),
)
def signal_change_point(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    all_series = _all_known_series(connection)
    output_rows: list[dict] = []
    fingerprint_parts: list = []
    for series in all_series:
        fingerprint_parts.append([series.metric_id, len(series.values), series.values[-5:]])
        for at_date in _change_points(series):
            output_rows.append(
                {"metric_id": series.metric_id, "at_date": at_date, "method": "pelt"}
            )
    return ComputeResult(input_fingerprint=fingerprint_value(fingerprint_parts), rows=output_rows)


@deriver(
    name="signal_anomaly",
    partition_class="GLOBAL",
    final_inference="COMPUTED",
    description="Robust z-score anomalies against the trailing_90d baseline (PHASE4_IMPLEMENTATION_PLAN.md).",
    output_relation="signal.anomaly",
    inputs=("measure_metric_daily", "measure_metric_daily_global"),
    clear_partition=_clear_all("signal.anomaly"),
)
def signal_anomaly(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    all_series = _all_known_series(connection)
    output_rows: list[dict] = []
    fingerprint_parts: list = []
    for series in all_series:
        fingerprint_parts.append([series.metric_id, len(series.values), series.values[-5:]])
        baselines = _trailing_90d_baselines(series)
        value_by_day = dict(zip(series.days, series.values, strict=True))
        for day, center, spread, _n_obs in baselines:
            if spread == 0:
                continue
            observed = value_by_day[day]
            z = (observed - center) / spread
            if abs(z) < _ANOMALY_Z_THRESHOLD:
                continue
            output_rows.append(
                {
                    "metric_id": series.metric_id,
                    "bucket_start": day,
                    "observed": observed,
                    "baseline_kind": "trailing_90d",
                    "expected_low": center - _ANOMALY_Z_THRESHOLD * spread,
                    "expected_high": center + _ANOMALY_Z_THRESHOLD * spread,
                    "z_score": z,
                    "method": "robust_z",
                }
            )
    return ComputeResult(input_fingerprint=fingerprint_value(fingerprint_parts), rows=output_rows)


__all__ = [
    "KnownSeries",
    "_all_known_series",
    "_change_points",
    "signal_anomaly",
    "signal_baseline",
    "signal_change_point",
]
