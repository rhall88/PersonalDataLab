from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from .fingerprints import (
    FINGERPRINT_VERSION,
    canonical_json_bytes,
    conversation_fingerprint,
    event_fingerprint,
)
from .migrations import LATEST_SCHEMA_VERSION
from .models import ImportResult, NormalizedConversation, NormalizedEvent, ParsedExport

Outcome = Literal["inserted", "updated", "unchanged", "stale", "tombstoned"]


class ReconciliationError(RuntimeError):
    """Raised when an import cannot be reconciled without ambiguity."""


@dataclass(frozen=True)
class _Authority:
    at: datetime | None
    kind: str


def create_database_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


@contextmanager
def _transaction(engine: Engine) -> Iterator[Connection]:
    with engine.begin() as connection:
        yield connection


def _run_key(parsed_exports: tuple[ParsedExport, ...]) -> bytes:
    payload = {
        "fingerprint_version": FINGERPRINT_VERSION,
        "policy_version": "cumulative-v1",
        "source": parsed_exports[0].source,
        "snapshot_scope": parsed_exports[0].snapshot_scope,
        "files": [
            {
                "parser_version": parsed.parser_version,
                "sha256": parsed.sha256,
            }
            for parsed in parsed_exports
        ],
    }
    return hashlib.sha256(canonical_json_bytes(payload)).digest()


def _validate_batch(parsed_exports: tuple[ParsedExport, ...]) -> None:
    if not parsed_exports:
        raise ValueError("at least one parsed export is required")
    source = parsed_exports[0].source
    scope = parsed_exports[0].snapshot_scope
    if any(parsed.source != source for parsed in parsed_exports):
        raise ValueError("all parsed exports in a run must have the same source")
    if any(parsed.snapshot_scope != scope for parsed in parsed_exports):
        raise ValueError("all parsed exports in a run must have the same snapshot scope")
    conversation_keys: set[tuple[str, str, str]] = set()
    event_keys: set[tuple[str, str, str]] = set()
    for parsed in parsed_exports:
        for conversation in parsed.conversations:
            conversation_key = (
                conversation.source,
                conversation.source_record_namespace,
                conversation.source_conversation_id,
            )
            if conversation_key in conversation_keys:
                raise ReconciliationError(
                    f"import rolled back: duplicate conversation identity {conversation_key}"
                )
            conversation_keys.add(conversation_key)
            for event in conversation.events:
                event_key = (
                    event.source,
                    event.source_record_namespace,
                    event.source_record_id,
                )
                if event_key in event_keys:
                    raise ReconciliationError(
                        f"import rolled back: duplicate event identity {event_key}"
                    )
                event_keys.add(event_key)
        for event in parsed.events:
            event_key = (
                event.source,
                event.source_record_namespace,
                event.source_record_id,
            )
            if event_key in event_keys:
                raise ReconciliationError(
                    f"import rolled back: duplicate event identity {event_key}"
                )
            event_keys.add(event_key)


def _require_latest_schema(connection: Connection) -> None:
    has_migrations = bool(
        connection.execute(
            text("SELECT to_regclass('schema_migrations') IS NOT NULL")
        ).scalar_one()
    )
    if not has_migrations:
        raise ReconciliationError("database schema is outdated; run personal-data-lab migrate")
    version = connection.execute(text("SELECT MAX(version) FROM schema_migrations")).scalar_one()
    if version is None or int(version) < LATEST_SCHEMA_VERSION:
        raise ReconciliationError(
            "database schema is outdated; run personal-data-lab migrate "
            f"(required {LATEST_SCHEMA_VERSION}, found {version or 0})"
        )


def _upsert_source_file(connection: Connection, parsed: ParsedExport) -> int:
    source_file_id = int(
        connection.execute(
            text(
                """
                INSERT INTO source_files
                    (source, filename, original_path, sha256, size_bytes, exported_at,
                     parser_version)
                VALUES
                    (:source, :filename, :original_path, :sha256, :size_bytes, :exported_at,
                     :parser_version)
                ON CONFLICT (source, sha256) DO UPDATE SET
                    last_seen_at = CURRENT_TIMESTAMP
                RETURNING id
                """
            ),
            {
                "source": parsed.source,
                "filename": parsed.filename,
                "original_path": parsed.original_path,
                "sha256": parsed.sha256,
                "size_bytes": parsed.size_bytes,
                "exported_at": parsed.exported_at,
                "parser_version": parsed.parser_version,
            },
        ).scalar_one()
    )
    connection.execute(
        text(
            """
            INSERT INTO source_file_locations (source_file_id, original_path)
            VALUES (:source_file_id, :original_path)
            ON CONFLICT (source_file_id, original_path) DO UPDATE SET
                last_seen_at = CURRENT_TIMESTAMP
            """
        ),
        {"source_file_id": source_file_id, "original_path": parsed.original_path},
    )
    return source_file_id


def _create_or_get_run(
    connection: Connection,
    parsed_exports: tuple[ParsedExport, ...],
    run_key: bytes,
) -> tuple[int, str, dict[str, Any]]:
    parser_version = "+".join(sorted({parsed.parser_version for parsed in parsed_exports}))
    exported_at = max(
        (parsed.exported_at for parsed in parsed_exports if parsed.exported_at),
        default=None,
    )
    export_ids = sorted({parsed.export_id for parsed in parsed_exports if parsed.export_id})
    export_id = "+".join(export_ids) if export_ids else None
    snapshot_kinds = {parsed.snapshot_kind for parsed in parsed_exports}
    snapshot_kind = snapshot_kinds.pop() if len(snapshot_kinds) == 1 else "partial"
    inserted = connection.execute(
        text(
            """
            INSERT INTO import_runs
                (source, export_id, run_key, snapshot_kind, snapshot_scope, exported_at,
                 parser_version, fingerprint_version, status)
            VALUES
                (:source, :export_id, :run_key, :snapshot_kind, :snapshot_scope, :exported_at,
                 :parser_version, :fingerprint_version, 'staging')
            ON CONFLICT (run_key) DO NOTHING
            RETURNING id, status, stats
            """
        ),
        {
            "source": parsed_exports[0].source,
            "export_id": export_id,
            "run_key": run_key,
            "snapshot_kind": snapshot_kind,
            "snapshot_scope": parsed_exports[0].snapshot_scope,
            "exported_at": exported_at,
            "parser_version": parser_version,
            "fingerprint_version": FINGERPRINT_VERSION,
        },
    ).one_or_none()
    if inserted is not None:
        return int(inserted.id), str(inserted.status), dict(inserted.stats)
    existing = connection.execute(
        text("SELECT id, status, stats FROM import_runs WHERE run_key = :run_key"),
        {"run_key": run_key},
    ).one()
    return int(existing.id), str(existing.status), dict(existing.stats)


def _record_run_files(
    connection: Connection,
    run_id: int,
    source_file_ids: tuple[int, ...],
    parsed_exports: tuple[ParsedExport, ...],
) -> None:
    for ordinal, (source_file_id, parsed) in enumerate(
        zip(source_file_ids, parsed_exports, strict=True)
    ):
        connection.execute(
            text(
                """
                INSERT INTO import_run_source_files
                    (import_run_id, source_file_id, category, ordinal)
                VALUES (:run_id, :source_file_id, :category, :ordinal)
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "run_id": run_id,
                "source_file_id": source_file_id,
                "category": parsed.filename,
                "ordinal": ordinal,
            },
        )


def _incoming_authority(record: Any, parsed: ParsedExport) -> _Authority:
    if record.authority_at is not None:
        return _Authority(record.authority_at, record.authority_kind)
    if record.provider_updated_at is not None:
        return _Authority(record.provider_updated_at, "provider_updated_at")
    if parsed.exported_at is not None:
        return _Authority(parsed.exported_at, "exported_at")
    return _Authority(None, "unknown")


def _changed_outcome(
    *,
    entity: str,
    logical_key: str,
    existing_fingerprint_version: str,
    existing_authority: _Authority,
    incoming_authority: _Authority,
) -> Literal["updated", "stale"]:
    if existing_fingerprint_version != FINGERPRINT_VERSION:
        return "updated"
    if existing_authority.kind == "unknown" and incoming_authority.kind != "unknown":
        return "updated"
    if existing_authority.kind != incoming_authority.kind:
        raise ReconciliationError(
            f"ambiguous {entity} change for {logical_key}: authority kinds are not comparable"
        )
    if existing_authority.at is None or incoming_authority.at is None:
        raise ReconciliationError(
            f"ambiguous {entity} change for {logical_key}: no authoritative ordering"
        )
    if incoming_authority.at > existing_authority.at:
        return "updated"
    if incoming_authority.at < existing_authority.at:
        return "stale"
    raise ReconciliationError(
        f"ambiguous {entity} change for {logical_key}: equal authority with different content"
    )


def _reconcile_conversation(
    connection: Connection,
    run_id: int,
    parsed: ParsedExport,
    conversation: NormalizedConversation,
) -> tuple[int, Outcome, bytes]:
    fingerprint = conversation_fingerprint(conversation)
    authority = _incoming_authority(conversation, parsed)
    existing = connection.execute(
        text(
            """
            SELECT id, content_fingerprint, fingerprint_version, authority_at, authority_kind,
                   lifecycle_state
            FROM conversations
            WHERE source = :source
              AND source_record_namespace = :namespace
              AND source_conversation_id = :source_id
            """
        ),
        {
            "source": conversation.source,
            "namespace": conversation.source_record_namespace,
            "source_id": conversation.source_conversation_id,
        },
    ).one_or_none()
    params = {
        "source": conversation.source,
        "namespace": conversation.source_record_namespace,
        "source_id": conversation.source_conversation_id,
        "title": conversation.title,
        "started_at": conversation.started_at,
        "ended_at": conversation.ended_at,
        "metadata": json.dumps(conversation.metadata),
        "fingerprint": fingerprint,
        "fingerprint_version": FINGERPRINT_VERSION,
        "provider_updated_at": conversation.provider_updated_at,
        "authority_at": authority.at,
        "authority_kind": authority.kind,
        "run_id": run_id,
        "lifecycle_state": conversation.lifecycle_state,
    }
    if existing is None:
        conversation_id = int(
            connection.execute(
                text(
                    """
                    INSERT INTO conversations
                        (source, source_record_namespace, source_conversation_id, title,
                         started_at, ended_at, metadata, content_fingerprint,
                         fingerprint_version, provider_updated_at, authority_at, authority_kind,
                         canonical_run_id, lifecycle_state, tombstoned_at, tombstone_run_id)
                    VALUES
                        (:source, :namespace, :source_id, :title, :started_at, :ended_at,
                         CAST(:metadata AS jsonb), :fingerprint, :fingerprint_version,
                         CAST(:provider_updated_at AS timestamptz),
                         CAST(:authority_at AS timestamptz), :authority_kind, :run_id,
                         :lifecycle_state,
                         CASE WHEN :lifecycle_state = 'provider_deleted'
                              THEN CAST(:authority_at AS timestamptz) END,
                         CASE WHEN :lifecycle_state = 'provider_deleted' THEN :run_id END)
                    RETURNING id
                    """
                ),
                params,
            ).scalar_one()
        )
        return conversation_id, "inserted", fingerprint
    conversation_id = int(existing.id)
    if bytes(existing.content_fingerprint) == fingerprint:
        return conversation_id, "unchanged", fingerprint
    outcome = _changed_outcome(
        entity="conversation",
        logical_key=(
            f"{conversation.source}/{conversation.source_record_namespace}/"
            f"{conversation.source_conversation_id}"
        ),
        existing_fingerprint_version=str(existing.fingerprint_version),
        existing_authority=_Authority(existing.authority_at, str(existing.authority_kind)),
        incoming_authority=authority,
    )
    if outcome == "stale":
        return conversation_id, outcome, fingerprint
    tombstoned = (
        str(existing.lifecycle_state) != "provider_deleted"
        and conversation.lifecycle_state == "provider_deleted"
    )
    connection.execute(
        text(
            """
            UPDATE conversations SET
                title = :title,
                started_at = :started_at,
                ended_at = :ended_at,
                metadata = CAST(:metadata AS jsonb),
                content_fingerprint = :fingerprint,
                fingerprint_version = :fingerprint_version,
                provider_updated_at = CAST(:provider_updated_at AS timestamptz),
                authority_at = CAST(:authority_at AS timestamptz),
                authority_kind = :authority_kind,
                canonical_run_id = :run_id,
                canonical_revision = canonical_revision + 1,
                canonical_updated_at = CURRENT_TIMESTAMP,
                lifecycle_state = :lifecycle_state,
                tombstoned_at = CASE
                    WHEN :lifecycle_state = 'provider_deleted'
                        THEN CAST(:authority_at AS timestamptz)
                    ELSE NULL
                END,
                tombstone_run_id = CASE
                    WHEN :lifecycle_state = 'provider_deleted' THEN :run_id
                    ELSE NULL
                END
            WHERE id = :id
              AND content_fingerprint IS DISTINCT FROM :fingerprint
            """
        ),
        params | {"id": conversation_id},
    )
    return conversation_id, "tombstoned" if tombstoned else "updated", fingerprint


def _reconcile_event(
    connection: Connection,
    run_id: int,
    source_file_id: int,
    parsed: ParsedExport,
    conversation: NormalizedConversation | None,
    conversation_id: int | None,
    event: NormalizedEvent,
) -> tuple[Outcome, bytes]:
    fingerprint = event_fingerprint(
        event,
        conversation_namespace=(conversation.source_record_namespace if conversation else None),
        conversation_source_id=(conversation.source_conversation_id if conversation else None),
    )
    authority = _incoming_authority(event, parsed)
    existing = connection.execute(
        text(
            """
            SELECT id, content_fingerprint, fingerprint_version, authority_at, authority_kind,
                   lifecycle_state
            FROM events
            WHERE source = :source
              AND source_record_namespace = :namespace
              AND source_record_id = :source_id
            """
        ),
        {
            "source": event.source,
            "namespace": event.source_record_namespace,
            "source_id": event.source_record_id,
        },
    ).one_or_none()
    params = {
        "occurred_at": event.occurred_at,
        "source": event.source,
        "event_type": event.event_type,
        "namespace": event.source_record_namespace,
        "actor_identity": event.actor_identity,
        "conversation_id": conversation_id,
        "text": event.text,
        "metadata": json.dumps(event.metadata),
        "source_id": event.source_record_id,
        "source_node_id": event.source_node_id,
        "parent_source_node_id": event.parent_source_node_id,
        "fingerprint": fingerprint,
        "fingerprint_version": FINGERPRINT_VERSION,
        "provider_updated_at": event.provider_updated_at,
        "authority_at": authority.at,
        "authority_kind": authority.kind,
        "run_id": run_id,
        "lifecycle_state": event.lifecycle_state,
    }
    if existing is None:
        event_id = int(
            connection.execute(
                text(
                    """
                    INSERT INTO events
                        (occurred_at, source, event_type, source_record_namespace,
                         actor_person_id, actor_identity, conversation_id, text, metadata,
                         source_record_id,
                         source_node_id, parent_source_node_id, content_fingerprint,
                         fingerprint_version, provider_updated_at, authority_at, authority_kind,
                         canonical_run_id, lifecycle_state, tombstoned_at, tombstone_run_id)
                    VALUES
                        (:occurred_at, :source, :event_type, :namespace, NULL,
                         :actor_identity, :conversation_id, :text, CAST(:metadata AS jsonb),
                         :source_id,
                         :source_node_id, :parent_source_node_id, :fingerprint,
                         :fingerprint_version, CAST(:provider_updated_at AS timestamptz),
                         CAST(:authority_at AS timestamptz),
                         :authority_kind, :run_id, :lifecycle_state,
                         CASE WHEN :lifecycle_state = 'provider_deleted'
                              THEN CAST(:authority_at AS timestamptz) END,
                         CASE WHEN :lifecycle_state = 'provider_deleted' THEN :run_id END)
                    RETURNING id
                    """
                ),
                params,
            ).scalar_one()
        )
        outcome: Outcome = "inserted"
    else:
        event_id = int(existing.id)
        if bytes(existing.content_fingerprint) == fingerprint:
            outcome = "unchanged"
        else:
            changed = _changed_outcome(
                entity="event",
                logical_key=f"{event.source}/{event.source_record_namespace}/{event.source_record_id}",
                existing_fingerprint_version=str(existing.fingerprint_version),
                existing_authority=_Authority(existing.authority_at, str(existing.authority_kind)),
                incoming_authority=authority,
            )
            if changed == "stale":
                outcome = "stale"
            else:
                tombstoned = (
                    str(existing.lifecycle_state) != "provider_deleted"
                    and event.lifecycle_state == "provider_deleted"
                )
                connection.execute(
                    text(
                        """
                        UPDATE events SET
                            occurred_at = :occurred_at,
                            event_type = :event_type,
                            actor_identity = :actor_identity,
                            conversation_id = :conversation_id,
                            text = :text,
                            metadata = CAST(:metadata AS jsonb),
                            source_node_id = :source_node_id,
                            parent_source_node_id = :parent_source_node_id,
                            content_fingerprint = :fingerprint,
                            fingerprint_version = :fingerprint_version,
                            provider_updated_at = CAST(:provider_updated_at AS timestamptz),
                            authority_at = CAST(:authority_at AS timestamptz),
                            authority_kind = :authority_kind,
                            canonical_run_id = :run_id,
                            canonical_revision = canonical_revision + 1,
                            canonical_updated_at = CURRENT_TIMESTAMP,
                            lifecycle_state = :lifecycle_state,
                            tombstoned_at = CASE
                                WHEN :lifecycle_state = 'provider_deleted'
                                    THEN CAST(:authority_at AS timestamptz)
                                ELSE NULL
                            END,
                            tombstone_run_id = CASE
                                WHEN :lifecycle_state = 'provider_deleted' THEN :run_id
                                ELSE NULL
                            END
                        WHERE id = :id
                          AND content_fingerprint IS DISTINCT FROM :fingerprint
                        """
                    ),
                    params | {"id": event_id},
                )
                outcome = "tombstoned" if tombstoned else "updated"
    connection.execute(
        text(
            """
            INSERT INTO source_file_events (source_file_id, event_id)
            VALUES (:source_file_id, :event_id)
            ON CONFLICT DO NOTHING
            """
        ),
        {"source_file_id": source_file_id, "event_id": event_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO event_observations
                (import_run_id, source_file_id, event_id, observed_content_fingerprint,
                 provider_updated_at, outcome)
            VALUES
                (:run_id, :source_file_id, :event_id, :fingerprint,
                 :provider_updated_at, :outcome)
            ON CONFLICT DO NOTHING
            """
        ),
        {
            "run_id": run_id,
            "source_file_id": source_file_id,
            "event_id": event_id,
            "fingerprint": fingerprint,
            "provider_updated_at": event.provider_updated_at,
            "outcome": outcome,
        },
    )
    return outcome, fingerprint


def _empty_counts() -> dict[str, int]:
    return {
        name: 0
        for name in ("inserted", "updated", "unchanged", "stale", "tombstoned")
    }


def _import_parsed_export(
    connection: Connection,
    parsed: ParsedExport,
    source_file_id: int,
    run_id: int,
) -> ImportResult:
    conversation_counts = _empty_counts()
    event_counts = _empty_counts()
    for conversation in parsed.conversations:
        conversation_id, outcome, fingerprint = _reconcile_conversation(
            connection, run_id, parsed, conversation
        )
        conversation_counts[outcome] += 1
        connection.execute(
            text(
                """
                INSERT INTO source_file_conversations (source_file_id, conversation_id)
                VALUES (:source_file_id, :conversation_id)
                ON CONFLICT DO NOTHING
                """
            ),
            {"source_file_id": source_file_id, "conversation_id": conversation_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO conversation_observations
                    (import_run_id, source_file_id, conversation_id,
                     observed_content_fingerprint, provider_updated_at, outcome)
                VALUES
                    (:run_id, :source_file_id, :conversation_id, :fingerprint,
                     :provider_updated_at, :outcome)
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "run_id": run_id,
                "source_file_id": source_file_id,
                "conversation_id": conversation_id,
                "fingerprint": fingerprint,
                "provider_updated_at": conversation.provider_updated_at,
                "outcome": outcome,
            },
        )
        for event in conversation.events:
            event_outcome, _ = _reconcile_event(
                connection,
                run_id,
                source_file_id,
                parsed,
                conversation,
                conversation_id,
                event,
            )
            event_counts[event_outcome] += 1

    for event in parsed.events:
        event_outcome, _ = _reconcile_event(
            connection, run_id, source_file_id, parsed, None, None, event
        )
        event_counts[event_outcome] += 1

    database_events = int(
        connection.execute(
            text(
                """
                SELECT COUNT(*) FROM event_observations
                WHERE import_run_id = :run_id AND source_file_id = :source_file_id
                """
            ),
            {"run_id": run_id, "source_file_id": source_file_id},
        ).scalar_one()
    )
    database_conversations = int(
        connection.execute(
            text(
                """
                SELECT COUNT(*) FROM conversation_observations
                WHERE import_run_id = :run_id AND source_file_id = :source_file_id
                """
            ),
            {"run_id": run_id, "source_file_id": source_file_id},
        ).scalar_one()
    )
    result = ImportResult(
        source_file_id=source_file_id,
        run_id=run_id,
        source_conversations=parsed.conversation_count,
        database_conversations=database_conversations,
        source_events=parsed.event_count,
        database_events=database_events,
        inserted_conversations=conversation_counts["inserted"],
        updated_conversations=conversation_counts["updated"],
        unchanged_conversations=conversation_counts["unchanged"],
        stale_conversations=conversation_counts["stale"],
        tombstoned_conversations=conversation_counts["tombstoned"],
        inserted_events=event_counts["inserted"],
        updated_events=event_counts["updated"],
        unchanged_events=event_counts["unchanged"],
        stale_events=event_counts["stale"],
        tombstoned_events=event_counts["tombstoned"],
    )
    if not result.reconciled:
        raise ReconciliationError(
            "import rolled back: "
            f"conversations {result.database_conversations}/{result.source_conversations}, "
            f"events {result.database_events}/{result.source_events}"
        )
    return result


def import_parsed_exports(
    engine: Engine,
    parsed_exports: tuple[ParsedExport, ...],
) -> tuple[ImportResult, ...]:
    _validate_batch(parsed_exports)
    run_key = _run_key(parsed_exports)
    with _transaction(engine) as connection:
        _require_latest_schema(connection)
        source_file_ids = tuple(
            _upsert_source_file(connection, parsed) for parsed in parsed_exports
        )
        run_id, status, stats = _create_or_get_run(connection, parsed_exports, run_key)
        _record_run_files(connection, run_id, source_file_ids, parsed_exports)
        if status == "completed":
            stored = stats.get("results")
            if not isinstance(stored, list):
                raise ReconciliationError(f"completed import run {run_id} has no stored results")
            return tuple(
                ImportResult.model_validate(item).model_copy(update={"short_circuited": True})
                for item in stored
            )
        if status != "staging":
            raise ReconciliationError(f"import run {run_id} is already {status}")
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:source), hashtext(:scope))"),
            {
                "source": parsed_exports[0].source,
                "scope": parsed_exports[0].snapshot_scope,
            },
        )
        connection.execute(
            text("UPDATE import_runs SET status = 'finalizing' WHERE id = :run_id"),
            {"run_id": run_id},
        )
        results = tuple(
            _import_parsed_export(connection, parsed, source_file_id, run_id)
            for parsed, source_file_id in zip(parsed_exports, source_file_ids, strict=True)
        )
        serialized = [result.model_dump(mode="json") for result in results]
        connection.execute(
            text(
                """
                UPDATE import_runs SET
                    status = 'completed',
                    completed_at = CURRENT_TIMESTAMP,
                    stats = CAST(:stats AS jsonb)
                WHERE id = :run_id
                """
            ),
            {"run_id": run_id, "stats": json.dumps({"results": serialized})},
        )
        return results


def import_openai_export(engine: Engine, parsed: ParsedExport) -> ImportResult:
    if parsed.source != "openai":
        raise ValueError("import_openai_export requires an OpenAI parsed export")
    return import_parsed_exports(engine, (parsed,))[0]
