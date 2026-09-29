"""Phase 4's interaction dynamics + interaction states (PHASE4_IMPLEMENTATION_PLAN.md decisions
9-10), scoped to facebook + imessages 1:1 threads only -- google has no conversation_id at all
(confirmed: 0 of 164,063 rows) and its actor_identity values are dominated by automated senders,
not correspondents; anthropic/openai are a chat with an assistant, not a person. A thread only
qualifies when it resolves to exactly one non-owner correspondent -- this project has no
multi-party attribution model, and section 6 of the architecture doesn't specify one either.

Both derivers below read directly from `events` rather than from each other's output table, for
the same reason signals.py's derivers do (see that module's docstring): the worker does not
guarantee relative execution order between two GLOBAL derivers dirtied in the same propagation
pass, so signal_interaction_state recomputes the monthly metrics via the same
_monthly_metrics() helper measure_interaction_dynamics uses, rather than reading
measure.interaction_dynamics.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import ruptures
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ...fingerprints import fingerprint_value
from ...identity_normalize import classify
from ..registry import ComputeResult, deriver

# A message following this much silence starts a new "exchange"; who sends the first message of
# an exchange is what initiation_share measures. 4 hours is long enough that a normal back-and-
# forth conversation isn't chopped into many "exchanges", short enough that a same-day but
# clearly separate check-in later counts as a new one. Documented, not derived from data --
# reviewable the same way the anomaly z-threshold and PELT penalty are.
_NEW_EXCHANGE_GAP = timedelta(hours=4)

# Below this many months of history, segmenting into interaction states isn't meaningful.
_MIN_STATE_MONTHS = 6
_STATE_PELT_PENALTY_SCALE = 3.0


@dataclass(frozen=True)
class ThreadMessage:
    conversation_id: int
    occurred_at: datetime
    direction: str  # "in" | "out"


def _facebook_owner_identity(connection: Connection) -> str | None:
    # Once correspondents are linked to people too, "any facebook link" is no longer the owner:
    # the owner is the linked identity present in the most conversations (every 1:1 thread).
    row = connection.execute(
        text(
            """
            SELECT il.identity_value
            FROM identity_links il
            JOIN events e ON e.source = 'facebook' AND e.actor_identity = il.identity_value
            WHERE il.source = 'facebook' AND il.lifecycle_state = 'active'
            GROUP BY il.identity_value
            ORDER BY count(DISTINCT e.conversation_id) DESC, il.identity_value
            LIMIT 1
            """
        )
    ).first()
    return row.identity_value if row else None


def _facebook_threads(connection: Connection) -> dict[tuple[str, str], list[ThreadMessage]]:
    owner = _facebook_owner_identity(connection)
    rows = connection.execute(
        text(
            """
            SELECT conversation_id, occurred_at, actor_identity
            FROM events
            WHERE source = 'facebook' AND lifecycle_state = 'active' AND event_type = 'message'
              AND conversation_id IS NOT NULL AND occurred_at IS NOT NULL
            ORDER BY conversation_id, occurred_at
            """
        )
    ).all()
    by_conv: dict[int, list] = {}
    correspondents: dict[int, set[str]] = {}
    for row in rows:
        by_conv.setdefault(row.conversation_id, []).append(row)
        if (
            row.actor_identity
            and row.actor_identity != owner
            and classify(row.actor_identity) != "role_label"
        ):
            correspondents.setdefault(row.conversation_id, set()).add(row.actor_identity)

    result: dict[tuple[str, str], list[ThreadMessage]] = {}
    for conv_id, msgs in by_conv.items():
        corr_set = correspondents.get(conv_id, set())
        if len(corr_set) != 1:
            continue  # group thread, or no resolvable correspondent -- excluded (decision 9)
        correspondent = next(iter(corr_set))
        thread_msgs = []
        for row in msgs:
            if row.actor_identity == owner:
                direction = "out"
            elif row.actor_identity == correspondent:
                direction = "in"
            else:
                continue  # a role-label sender (e.g. a deleted participant) -- not attributable
            thread_msgs.append(ThreadMessage(conv_id, row.occurred_at, direction))
        if thread_msgs:
            key = ("facebook", correspondent)
            result.setdefault(key, []).extend(thread_msgs)
    return result


def _imessages_threads(connection: Connection) -> dict[tuple[str, str], list[ThreadMessage]]:
    # message.outgoing always carries the literal actor_identity "unknown" (imessages' importer
    # never resolves the account owner's own identity), never a real value -- so only
    # message.incoming rows can identify who a thread's correspondent is.
    rows = connection.execute(
        text(
            """
            SELECT conversation_id, occurred_at, event_type, actor_identity
            FROM events
            WHERE source = 'imessages' AND lifecycle_state = 'active'
              AND event_type IN ('message.incoming', 'message.outgoing')
              AND conversation_id IS NOT NULL AND occurred_at IS NOT NULL
            ORDER BY conversation_id, occurred_at
            """
        )
    ).all()
    by_conv: dict[int, list] = {}
    correspondents: dict[int, set[str]] = {}
    for row in rows:
        by_conv.setdefault(row.conversation_id, []).append(row)
        if row.event_type == "message.incoming" and row.actor_identity:
            correspondents.setdefault(row.conversation_id, set()).add(row.actor_identity)

    result: dict[tuple[str, str], list[ThreadMessage]] = {}
    for conv_id, msgs in by_conv.items():
        corr_set = correspondents.get(conv_id, set())
        if len(corr_set) != 1:
            continue  # group thread, or never replied to -- excluded (decision 9)
        correspondent = next(iter(corr_set))
        thread_msgs = [
            ThreadMessage(
                conv_id, row.occurred_at, "in" if row.event_type == "message.incoming" else "out"
            )
            for row in msgs
        ]
        key = ("imessages", correspondent)
        result.setdefault(key, []).extend(thread_msgs)
    return result


def _all_correspondent_threads(connection: Connection) -> dict[tuple[str, str], list[ThreadMessage]]:
    result: dict[tuple[str, str], list[ThreadMessage]] = {}
    result.update(_facebook_threads(connection))
    result.update(_imessages_threads(connection))
    return result


def _monthly_metrics(messages: list[ThreadMessage]) -> dict[date, dict]:
    """Per-month messages_in/out, reciprocity_index (min/max of in,out), initiation_share (share
    of new exchanges -- a message after >=_NEW_EXCHANGE_GAP of silence -- sent by the account
    owner), and median_response_latency_seconds (median gap between consecutive messages that
    switch sender, either direction), per decision 9. Messages are merged and re-sorted
    chronologically across every conversation_id this correspondent appears in, since a gap or a
    sender switch has to be judged against the real timeline, not per-conversation order.
    """
    ordered = sorted(messages, key=lambda m: m.occurred_at)
    buckets: dict[date, dict] = {}
    prev: ThreadMessage | None = None
    for msg in ordered:
        month = date(msg.occurred_at.year, msg.occurred_at.month, 1)
        bucket = buckets.setdefault(
            month,
            {
                "in": 0, "out": 0, "conversations": set(),
                "new_exchanges_total": 0, "new_exchanges_out": 0, "latencies": [],
            },
        )
        bucket["conversations"].add(msg.conversation_id)
        bucket["in" if msg.direction == "in" else "out"] += 1

        is_new_exchange = prev is None or (msg.occurred_at - prev.occurred_at) >= _NEW_EXCHANGE_GAP
        if is_new_exchange:
            bucket["new_exchanges_total"] += 1
            if msg.direction == "out":
                bucket["new_exchanges_out"] += 1
        elif prev.direction != msg.direction:
            bucket["latencies"].append((msg.occurred_at - prev.occurred_at).total_seconds())
        prev = msg

    out: dict[date, dict] = {}
    for month, b in buckets.items():
        messages_in, messages_out = b["in"], b["out"]
        largest = max(messages_in, messages_out)
        reciprocity = (min(messages_in, messages_out) / largest) if largest > 0 else 0.0
        initiation_share = (
            b["new_exchanges_out"] / b["new_exchanges_total"] if b["new_exchanges_total"] > 0 else None
        )
        latency = statistics.median(b["latencies"]) if b["latencies"] else None
        out[month] = {
            "messages_in": messages_in,
            "messages_out": messages_out,
            "n_conversations": len(b["conversations"]),
            "reciprocity_index": reciprocity,
            "initiation_share": initiation_share,
            "median_response_latency_seconds": latency,
        }
    return out


def _mean_or_none(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return statistics.fmean(present) if present else None


def _state_breakpoints(series: list[float]) -> list[int]:
    n = len(series)
    spread = statistics.pstdev(series)
    if spread == 0:
        return [n]
    normalized = np.array([(v - statistics.fmean(series)) / spread for v in series])
    penalty = _STATE_PELT_PENALTY_SCALE * np.log(n)
    algo = ruptures.Pelt(model="l2", jump=1, min_size=3).fit(normalized)
    return algo.predict(pen=penalty)


def _clear_all(table: str):
    def _clear(connection: Connection, partition_key: str) -> None:
        connection.execute(text(f"DELETE FROM {table}"))

    return _clear


@deriver(
    name="measure_interaction_dynamics",
    partition_class="GLOBAL",
    final_inference="COMPUTED",
    description=(
        "Per-(source, correspondent, month) interaction metrics for 1:1 facebook/imessages "
        "threads (PHASE4_IMPLEMENTATION_PLAN.md)."
    ),
    output_relation="measure.interaction_dynamics",
    inputs=("measure_coverage",),
    clear_partition=_clear_all("measure.interaction_dynamics"),
)
def measure_interaction_dynamics(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    all_threads = _all_correspondent_threads(connection)
    output_rows: list[dict] = []
    fingerprint_parts: list = []
    for (source, correspondent), messages in all_threads.items():
        monthly = _monthly_metrics(messages)
        fingerprint_parts.append(
            [source, correspondent, len(messages), sorted(m.isoformat() for m in monthly)]
        )
        for month, metrics in monthly.items():
            output_rows.append(
                {"source": source, "correspondent": correspondent, "bucket_start": month, **metrics}
            )
    return ComputeResult(input_fingerprint=fingerprint_value(fingerprint_parts), rows=output_rows)


@deriver(
    name="signal_interaction_state",
    partition_class="GLOBAL",
    final_inference="COMPUTED",
    description=(
        "PELT segmentation of each correspondent's monthly reciprocity_index into interaction "
        "states, described by the mean of every interaction metric per state (PHASE4_"
        "IMPLEMENTATION_PLAN.md decision 10)."
    ),
    output_relation="signal.interaction_state",
    inputs=("measure_coverage",),
    clear_partition=_clear_all("signal.interaction_state"),
)
def signal_interaction_state(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    all_threads = _all_correspondent_threads(connection)
    output_rows: list[dict] = []
    fingerprint_parts: list = []
    for (source, correspondent), messages in all_threads.items():
        monthly = _monthly_metrics(messages)
        months_sorted = sorted(monthly)
        fingerprint_parts.append([source, correspondent, len(months_sorted)])
        if len(months_sorted) < _MIN_STATE_MONTHS:
            continue
        reciprocity_series = [monthly[m]["reciprocity_index"] for m in months_sorted]
        breakpoints = _state_breakpoints(reciprocity_series)
        start_idx = 0
        for end_idx in breakpoints:
            if end_idx <= start_idx:
                continue
            segment_months = months_sorted[start_idx:end_idx]
            seg = [monthly[m] for m in segment_months]
            output_rows.append(
                {
                    "source": source,
                    "correspondent": correspondent,
                    "state_start": segment_months[0],
                    "state_end": segment_months[-1],
                    "mean_messages_in": statistics.fmean(m["messages_in"] for m in seg),
                    "mean_messages_out": statistics.fmean(m["messages_out"] for m in seg),
                    "mean_reciprocity_index": statistics.fmean(m["reciprocity_index"] for m in seg),
                    "mean_initiation_share": _mean_or_none(m["initiation_share"] for m in seg),
                    "mean_median_response_latency_seconds": _mean_or_none(
                        m["median_response_latency_seconds"] for m in seg
                    ),
                    "n_months": len(segment_months),
                }
            )
            start_idx = end_idx
    return ComputeResult(input_fingerprint=fingerprint_value(fingerprint_parts), rows=output_rows)


__all__ = [
    "ThreadMessage",
    "_all_correspondent_threads",
    "_monthly_metrics",
    "measure_interaction_dynamics",
    "signal_interaction_state",
]
