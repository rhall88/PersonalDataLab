"""Phase 3's per-source and cross-source daily metrics (PHASE3_IMPLEMENTATION_PLAN.md): the
life ledger. Metrics ride Phase 0's existing deriver framework unchanged -- LOCAL per-source
derivers depending on measure_coverage, one GLOBAL deriver for the three cross-source
composites -- no new invalidation logic (plan Decision 2).

Every metric's day is gated by measure.coverage row existence for the (source, 'event') pairs it
depends on: a day Phase 1 has not attested as covered gets no measure.metric_daily row at all,
never a fabricated value = 0 (migration 011's convention, extended by migration 013; plan
Decisions 4-5).

METRIC_CATALOG and COMPOSITE_CATALOG are code, not schema (same reasoning as derive.deriver,
migration 010's own comment on this) -- sync_metric_catalog() upserts them into
derive.metric_def / derive.metric_dependency, called from derive.sync() alongside
DeriverRegistry.sync().
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ...fingerprints import fingerprint_value
from ...identity_normalize import classify
from ..partitions import parse_local_key
from ..registry import ComputeResult, deriver
from ..time_norm import source_timezone


@dataclass(frozen=True)
class MetricSpec:
    name: str
    description: str
    unit: str
    source: str
    object_kind: str
    kind: str  # "count" | "count_distinct_conversation"
    event_types: tuple[str, ...]


@dataclass(frozen=True)
class CompositeSpec:
    name: str
    description: str
    unit: str
    # Names of METRIC_CATALOG entries to sum, known only when every one has a row that day
    # (Decision 5). Empty for communication.unique_correspondents_count, which is computed
    # directly from events rather than summed from other metrics.
    constituents: tuple[str, ...]
    # Added when known that day but never required: a source that only fills gaps in a required
    # one (ios_messages beside imessages) must not make the total unknown on days it has no row.
    optional: tuple[str, ...] = ()


# 20 per-source metrics, grounded directly against the real database's (source, event_type)
# breakdown -- see PHASE3_IMPLEMENTATION_PLAN.md's grounding section for the counts.
METRIC_CATALOG: tuple[MetricSpec, ...] = (
    MetricSpec(
        "facebook.messages_count", "Facebook direct messages sent or received that day.",
        "count", "facebook", "event", "count", ("message",),
    ),
    MetricSpec(
        "facebook.calls_count", "Facebook voice/video calls that day.",
        "count", "facebook", "event", "count", ("call",),
    ),
    MetricSpec(
        "facebook.active_conversations_count",
        "Distinct Facebook message/call conversations touched that day.",
        "count", "facebook", "event", "count_distinct_conversation", ("message", "call"),
    ),
    MetricSpec(
        "imessages.incoming_count", "iMessages received that day.",
        "count", "imessages", "event", "count", ("message.incoming",),
    ),
    MetricSpec(
        "imessages.outgoing_count", "iMessages sent that day.",
        "count", "imessages", "event", "count", ("message.outgoing",),
    ),
    MetricSpec(
        "imessages.reactions_count", "iMessage reactions (tapbacks) that day.",
        "count", "imessages", "event", "count", ("message.reaction",),
    ),
    MetricSpec(
        "imessages.edits_count", "iMessage edits that day.",
        "count", "imessages", "event", "count", ("message.edit",),
    ),
    MetricSpec(
        "imessages.active_conversations_count",
        "Distinct iMessage conversations touched that day.",
        "count", "imessages", "event", "count_distinct_conversation",
        (
            "message.incoming", "message.outgoing", "message.reaction", "message.edit",
            "message.notification", "message.reaction_removed", "message.deletion",
        ),
    ),
    # The device's own Messages database holds only the iMessages the CSV export lacks (see
    # ios_backup/messages.py), so these count messages on top of imessages.*, never the same ones.
    MetricSpec(
        "ios_messages.incoming_count", "iMessages received that day that only the phone's own database has.",
        "count", "ios_messages", "event", "count", ("message.incoming",),
    ),
    MetricSpec(
        "ios_messages.outgoing_count", "iMessages sent that day that only the phone's own database has.",
        "count", "ios_messages", "event", "count", ("message.outgoing",),
    ),
    MetricSpec(
        "ios_messages.reactions_count", "iMessage reactions that day that only the phone's own database has.",
        "count", "ios_messages", "event", "count", ("message.reaction",),
    ),
    MetricSpec(
        "google.mail_messages_count", "Gmail messages that day.",
        "count", "google", "event", "count", ("mail_message",),
    ),
    MetricSpec(
        "google.chat_messages_count", "Google Chat messages that day.",
        "count", "google", "event", "count", ("chat_message",),
    ),
    MetricSpec(
        "google.voice_communications_count", "Google Voice communications that day.",
        "count", "google", "event", "count", ("voice_communication",),
    ),
    MetricSpec(
        "google.meet_calls_count", "Google Meet calls that day.",
        "count", "google", "event", "count", ("meet_call",),
    ),
    MetricSpec(
        "google.active_conversations_count",
        "Distinct Gmail/Google Chat conversations touched that day.",
        "count", "google", "event", "count_distinct_conversation", ("mail_message", "chat_message"),
    ),
    MetricSpec(
        "anthropic.chat_turns_count", "Anthropic conversation turns that day.",
        "count", "anthropic", "event", "count", ("conversation_message",),
    ),
    MetricSpec(
        "anthropic.active_conversations_count",
        "Distinct Anthropic conversations touched that day.",
        "count", "anthropic", "event", "count_distinct_conversation", ("conversation_message",),
    ),
    MetricSpec(
        "openai.chat_turns_count", "OpenAI conversation turns that day.",
        "count", "openai", "event", "count", ("conversation_message",),
    ),
    MetricSpec(
        "openai.active_conversations_count",
        "Distinct OpenAI conversations touched that day.",
        "count", "openai", "event", "count_distinct_conversation", ("conversation_message",),
    ),
)

# communication.unique_correspondents_count's fixed cross-source dependency, since it has no
# METRIC_CATALOG constituents to derive dependencies from. Counts distinct (source,
# actor_identity) pairs, not resolved real people -- see the module-level note below.
_UNIQUE_CORRESPONDENTS_SOURCES: dict[str, tuple[str, ...]] = {
    "facebook": ("message", "call"),
    "imessages": ("message.incoming", "message.outgoing"),
    "ios_messages": ("message.incoming", "message.outgoing"),
    "google": ("mail_message", "chat_message", "voice_communication", "meet_call"),
}
# The device Messages source shares the CSV source's handles: a correspondent seen in both is one
# iMessage correspondent, not two. It only fills gaps, so its coverage is not required for a day.
_CORRESPONDENT_SOURCE_ALIAS = {"ios_messages": "imessages"}
_OPTIONAL_CORRESPONDENT_SOURCES = frozenset({"ios_messages"})

COMPOSITE_CATALOG: tuple[CompositeSpec, ...] = (
    CompositeSpec(
        "messaging.total_messages_count",
        "Total message-like communication (Facebook messages, iMessages from both the CSV export "
        "and the phone's own database, Gmail, Google Chat) "
        "that day.",
        "count",
        (
            "facebook.messages_count", "imessages.incoming_count", "imessages.outgoing_count",
            "google.mail_messages_count", "google.chat_messages_count",
        ),
        optional=("ios_messages.incoming_count", "ios_messages.outgoing_count"),
    ),
    CompositeSpec(
        "ai_chat.total_turns_count",
        "Total AI chat turns (Anthropic + OpenAI) that day.",
        "count",
        ("anthropic.chat_turns_count", "openai.chat_turns_count"),
    ),
    CompositeSpec(
        "communication.unique_correspondents_count",
        "Distinct (source, correspondent) pairs contacted via Facebook, iMessages, or Google "
        "that day, excluding role labels and the account-owner sentinel. Counts correspondent "
        "IDENTITIES, not resolved real people: Phase 2's rules tier found zero real cross-source "
        "auto-links, and its two real name-candidates (angela barnes, sara slaughter) are still "
        "queued for review, so merging through identity_links here would be unfounded.",
        "count",
        (),
    ),
)

_METRIC_LOOKUP: dict[str, MetricSpec] = {spec.name: spec for spec in METRIC_CATALOG}


def _dependencies_for(spec: MetricSpec | CompositeSpec) -> set[tuple[str, str]]:
    if isinstance(spec, MetricSpec):
        return {(spec.source, spec.object_kind)}
    if not spec.constituents:
        return {(source, "event") for source in _UNIQUE_CORRESPONDENTS_SOURCES}
    deps: set[tuple[str, str]] = set()
    for name in (*spec.constituents, *spec.optional):
        deps |= _dependencies_for(_METRIC_LOOKUP[name])
    return deps


def sync_metric_catalog(connection: Connection) -> None:
    """Upsert METRIC_CATALOG/COMPOSITE_CATALOG into derive.metric_def/metric_dependency. Called
    from derive.sync() alongside DeriverRegistry.sync() (Decision 3) -- metric shape is code,
    not schema.
    """
    for spec in (*METRIC_CATALOG, *COMPOSITE_CATALOG):
        metric_id = connection.execute(
            text(
                """
                INSERT INTO derive.metric_def (name, description, unit)
                VALUES (:name, :description, :unit)
                ON CONFLICT (name) DO UPDATE SET
                    description = EXCLUDED.description, unit = EXCLUDED.unit
                RETURNING id
                """
            ),
            {"name": spec.name, "description": spec.description, "unit": spec.unit},
        ).scalar_one()
        connection.execute(
            text("DELETE FROM derive.metric_dependency WHERE metric_id = :id"), {"id": metric_id}
        )
        for source, object_kind in sorted(_dependencies_for(spec)):
            connection.execute(
                text(
                    "INSERT INTO derive.metric_dependency (metric_id, source, object_kind) "
                    "VALUES (:id, :source, :kind) ON CONFLICT DO NOTHING"
                ),
                {"id": metric_id, "source": source, "kind": object_kind},
            )


def _metric_ids(connection: Connection, names: list[str]) -> dict[str, int]:
    return dict(
        connection.execute(
            text("SELECT name, id FROM derive.metric_def WHERE name = ANY(:names)"),
            {"names": names},
        ).all()
    )


def _covered_days(connection: Connection, source: str, start: date, end: date) -> list[date]:
    return list(
        connection.execute(
            text(
                "SELECT bucket_start FROM measure.coverage WHERE source = :source "
                "AND object_kind = 'event' AND bucket_start >= :start AND bucket_start < :end "
                "ORDER BY bucket_start"
            ),
            {"source": source, "start": start, "end": end},
        ).scalars()
    )


def _clear_metric_daily(connection: Connection, partition_key: str) -> None:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        return  # the undated bucket never has metric_daily rows; bucket_start is NOT NULL
    names = [spec.name for spec in METRIC_CATALOG if spec.source == source]
    if not names:
        return
    connection.execute(
        text(
            "DELETE FROM measure.metric_daily WHERE metric_id IN "
            "(SELECT id FROM derive.metric_def WHERE name = ANY(:names)) "
            "AND bucket_start >= :start AND bucket_start < :end"
        ),
        {"names": names, "start": start, "end": end},
    )


@deriver(
    name="measure_metric_daily",
    partition_class="LOCAL",
    final_inference="COMPUTED",
    description=(
        "Per-source daily communication/AI-chat metrics (PHASE3_IMPLEMENTATION_PLAN.md), "
        "gated on measure.coverage."
    ),
    output_relation="measure.metric_daily",
    inputs=("measure_coverage",),
    clear_partition=_clear_metric_daily,
)
def measure_metric_daily(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        return ComputeResult(input_fingerprint=fingerprint_value(["undated"]), rows=[])

    specs = tuple(spec for spec in METRIC_CATALOG if spec.source == source)
    if not specs:
        return ComputeResult(input_fingerprint=fingerprint_value(["no-metrics", source]), rows=[])

    covered_days = _covered_days(connection, source, start, end)
    if not covered_days:
        fp = fingerprint_value(["no-coverage", source, start.isoformat(), end.isoformat()])
        return ComputeResult(input_fingerprint=fp, rows=[])

    timezone_name = source_timezone(connection, source)
    event_rows = connection.execute(
        text(
            """
            SELECT id, canonical_revision, event_type, conversation_id,
                   (occurred_at AT TIME ZONE :tz)::date AS day
            FROM events
            WHERE source = :source
              AND lifecycle_state = 'active'
              AND occurred_at IS NOT NULL
              AND NOT (metadata ? 'csv_twin')
              AND (occurred_at AT TIME ZONE :tz)::date >= :start
              AND (occurred_at AT TIME ZONE :tz)::date < :end
            ORDER BY id
            """
        ),
        {"source": source, "tz": timezone_name, "start": start, "end": end},
    ).all()

    metric_ids = _metric_ids(connection, [spec.name for spec in specs])

    by_day: dict[date, list] = {}
    for row in event_rows:
        by_day.setdefault(row.day, []).append(row)

    output_rows: list[dict] = []
    for day in covered_days:
        day_rows = by_day.get(day, [])
        for spec in specs:
            matching = [r for r in day_rows if r.event_type in spec.event_types]
            if spec.kind == "count":
                value = len(matching)
            else:  # count_distinct_conversation
                value = len({r.conversation_id for r in matching if r.conversation_id is not None})
            output_rows.append(
                {
                    "metric_id": metric_ids[spec.name],
                    "bucket_start": day,
                    "value": value,
                    "computed_epoch_id": epoch_id,
                }
            )

    fingerprint_parts = [[r.id, r.canonical_revision] for r in event_rows]
    fingerprint = fingerprint_value([fingerprint_parts, [d.isoformat() for d in covered_days]])
    return ComputeResult(input_fingerprint=fingerprint, rows=output_rows)


def _clear_metric_daily_global(connection: Connection, partition_key: str) -> None:
    names = [spec.name for spec in COMPOSITE_CATALOG]
    connection.execute(
        text(
            "DELETE FROM measure.metric_daily WHERE metric_id IN "
            "(SELECT id FROM derive.metric_def WHERE name = ANY(:names))"
        ),
        {"names": names},
    )


@deriver(
    name="measure_metric_daily_global",
    partition_class="GLOBAL",
    final_inference="COMPUTED",
    description=(
        "Cross-source composite daily metrics (PHASE3_IMPLEMENTATION_PLAN.md): sums of "
        "per-source metrics plus unique correspondents. Run only by `derive run --global`."
    ),
    output_relation="measure.metric_daily",
    inputs=("measure_metric_daily",),
    clear_partition=_clear_metric_daily_global,
    # v2: messaging.total_messages_count also sums ios_messages.*; unique correspondents count a
    # handle seen in imessages and ios_messages once.
    code_version="2",
)
def measure_metric_daily_global(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    metric_ids = dict(connection.execute(text("SELECT name, id FROM derive.metric_def")).all())
    output_rows: list[dict] = []
    fingerprint_parts: list = []

    for spec in COMPOSITE_CATALOG:
        if not spec.constituents:
            continue
        constituent_ids = sorted(metric_ids[name] for name in spec.constituents)
        optional_ids = sorted(metric_ids[name] for name in spec.optional)
        per_day = connection.execute(
            text(
                """
                SELECT bucket_start,
                       array_agg(metric_id ORDER BY metric_id) FILTER (WHERE metric_id = ANY(:required))
                           AS present_ids,
                       SUM(value) AS total
                FROM measure.metric_daily
                WHERE metric_id = ANY(:ids)
                GROUP BY bucket_start
                """
            ),
            {"ids": constituent_ids + optional_ids, "required": constituent_ids},
        ).all()
        fingerprint_parts.append(
            [
                spec.name,
                [[row.bucket_start.isoformat(), list(row.present_ids or []), str(row.total)] for row in per_day],
            ]
        )
        for row in per_day:
            if list(row.present_ids or []) != constituent_ids:
                continue  # not every constituent known that day -- no fabricated partial sum
            output_rows.append(
                {
                    "metric_id": metric_ids[spec.name],
                    "bucket_start": row.bucket_start,
                    "value": row.total,
                    "computed_epoch_id": epoch_id,
                }
            )

    # communication.unique_correspondents_count: distinct (source, actor_identity) pairs across
    # facebook/imessages/google, excluding role labels, on days every one of the three sources
    # has attested 'event' coverage for.
    days_by_source: dict[str, set[date]] = {}
    pairs_by_source: dict[str, list[tuple[date, str]]] = {}
    for source, event_types in _UNIQUE_CORRESPONDENTS_SOURCES.items():
        days_by_source[source] = set(
            connection.execute(
                text(
                    "SELECT bucket_start FROM measure.coverage "
                    "WHERE source = :source AND object_kind = 'event'"
                ),
                {"source": source},
            ).scalars()
        )
        timezone_name = source_timezone(connection, source)
        rows = connection.execute(
            text(
                """
                SELECT (occurred_at AT TIME ZONE :tz)::date AS day, actor_identity
                FROM events
                WHERE source = :source AND lifecycle_state = 'active'
                  AND occurred_at IS NOT NULL AND event_type = ANY(:types)
                  AND actor_identity IS NOT NULL AND NOT (metadata ? 'csv_twin')
                """
            ),
            {"source": source, "tz": timezone_name, "types": list(event_types)},
        ).all()
        pairs_by_source[source] = [(row.day, row.actor_identity) for row in rows]

    required_days = [d for s, d in days_by_source.items() if s not in _OPTIONAL_CORRESPONDENT_SOURCES]
    common_days = set.intersection(*required_days) if required_days else set()
    correspondents_by_day: dict[date, set[tuple[str, str]]] = {}
    for source, pairs in pairs_by_source.items():
        for day, actor_identity in pairs:
            if day not in common_days:
                continue
            if classify(actor_identity) == "role_label":
                continue
            correspondents_by_day.setdefault(day, set()).add(
                (_CORRESPONDENT_SOURCE_ALIAS.get(source, source), actor_identity)
            )

    unique_metric_id = metric_ids["communication.unique_correspondents_count"]
    fingerprint_parts.append(
        [
            "unique_correspondents",
            sorted(d.isoformat() for d in common_days),
            sorted(
                [d.isoformat(), sorted(f"{s}:{v}" for s, v in pairs)]
                for d, pairs in correspondents_by_day.items()
            ),
        ]
    )
    for day in common_days:
        output_rows.append(
            {
                "metric_id": unique_metric_id,
                "bucket_start": day,
                "value": len(correspondents_by_day.get(day, set())),
                "computed_epoch_id": epoch_id,
            }
        )

    fingerprint = fingerprint_value(fingerprint_parts)
    return ComputeResult(input_fingerprint=fingerprint, rows=output_rows)
