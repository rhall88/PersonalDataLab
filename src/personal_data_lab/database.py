from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from .fingerprints import (
    FINGERPRINT_VERSION,
    asset_canonical_payload,
    asset_fingerprint,
    canonical_json_bytes,
    conversation_canonical_payload,
    conversation_fingerprint,
    event_canonical_payload,
    event_fingerprint,
    record_canonical_payload,
    record_fingerprint,
)
from .migrations import LATEST_SCHEMA_VERSION
from .models import (
    ImportResult,
    NormalizedAsset,
    NormalizedConversation,
    NormalizedEvent,
    NormalizedRecord,
    ParsedExport,
)
from .reconciliation import Authority, authority_advances, classify_changed_authority

Outcome = Literal[
    "inserted", "updated", "unchanged", "stale", "conflicted", "tombstoned"
]
RECONCILIATION_POLICY_VERSION = "lossless-cumulative-v2"


class ReconciliationError(RuntimeError):
    """Raised when an import cannot be reconciled without ambiguity."""


def create_database_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


@contextmanager
def _transaction(engine: Engine) -> Iterator[Connection]:
    with engine.begin() as connection:
        yield connection


def _run_key(parsed_exports: tuple[ParsedExport, ...]) -> bytes:
    payload = {
        "fingerprint_version": FINGERPRINT_VERSION,
        "policy_version": RECONCILIATION_POLICY_VERSION,
        "source": parsed_exports[0].source,
        "snapshot_scope": parsed_exports[0].snapshot_scope,
        "files": sorted(
            [
                {
                    "category": parsed.category,
                    "export_id": parsed.export_id,
                    "exported_at": parsed.exported_at,
                    "parser_version": parsed.parser_version,
                    "sha256": parsed.sha256,
                    "snapshot_kind": parsed.snapshot_kind,
                }
                for parsed in parsed_exports
            ],
            key=lambda item: (
                item["sha256"],
                item["parser_version"],
                item["category"] or "",
            ),
        ),
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
    file_keys = [(parsed.source, parsed.sha256) for parsed in parsed_exports]
    if len(file_keys) != len(set(file_keys)):
        raise ReconciliationError("a source file may appear only once in an import run")
    conversation_keys: set[tuple[str, str, str]] = set()
    event_keys: set[tuple[str, str, str]] = set()
    record_keys: set[tuple[str, str, str]] = set()
    asset_keys: set[tuple[str, str, str]] = set()
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
        for record in parsed.records:
            record_key = (
                record.source,
                record.source_record_namespace,
                record.source_record_id,
            )
            if record_key in record_keys:
                raise ReconciliationError(
                    f"import rolled back: duplicate record identity {record_key}"
                )
            record_keys.add(record_key)
        for asset in parsed.assets:
            asset_key = (asset.source, asset.asset_namespace, asset.source_asset_id)
            if asset_key in asset_keys:
                raise ReconciliationError(
                    f"import rolled back: duplicate asset identity {asset_key}"
                )
            asset_keys.add(asset_key)


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
                 parser_version, fingerprint_version, policy_version, status)
            VALUES
                (:source, :export_id, :run_key, :snapshot_kind, :snapshot_scope, :exported_at,
                 :parser_version, :fingerprint_version, :policy_version, 'staging')
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
            "policy_version": RECONCILIATION_POLICY_VERSION,
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
                "category": parsed.category,
                "ordinal": ordinal,
            },
        )


def _incoming_authority(record: Any, parsed: ParsedExport) -> Authority:
    if parsed.snapshot_kind == "complete" and parsed.exported_at is not None:
        return Authority(parsed.exported_at, "exported_at")
    if record.authority_at is not None:
        return Authority(record.authority_at, record.authority_kind)
    if record.provider_updated_at is not None:
        return Authority(record.provider_updated_at, "provider_updated_at")
    if parsed.exported_at is not None:
        return Authority(parsed.exported_at, "exported_at")
    return Authority(None, "unknown")


def _require_matching_fingerprint_version(
    *, entity: str, logical_id: str, stored_version: str
) -> None:
    if stored_version != FINGERPRINT_VERSION:
        raise ReconciliationError(
            f"cannot reconcile {entity} {logical_id}: database fingerprints use "
            f"{stored_version}; importer uses {FINGERPRINT_VERSION}; run the required "
            "database migration"
        )


def _persist_version(
    connection: Connection,
    *,
    table: Literal[
        "conversation_versions", "event_versions", "record_versions", "asset_versions"
    ],
    record_column: Literal["conversation_id", "event_id", "record_id", "asset_id"],
    record_id: int,
    fingerprint: bytes,
    payload: dict[str, Any],
    run_id: int,
    provider_updated_at: datetime | None,
    authority: Authority,
    lifecycle_state: str,
) -> tuple[int, bool]:
    inserted = connection.execute(
        text(
            f"""
            INSERT INTO {table}
                ({record_column}, content_fingerprint, fingerprint_version,
                 normalized_payload, payload_fidelity, first_observed_run_id,
                 last_observed_run_id, provider_updated_at, authority_at,
                 authority_kind, lifecycle_state)
            VALUES
                (:record_id, :fingerprint, :fingerprint_version, CAST(:payload AS jsonb),
                 'full', :run_id, :run_id, CAST(:provider_updated_at AS timestamptz),
                 CAST(:authority_at AS timestamptz), :authority_kind, :lifecycle_state)
            ON CONFLICT ({record_column}, fingerprint_version, content_fingerprint)
                DO NOTHING
            RETURNING id
            """
        ),
        {
            "record_id": record_id,
            "fingerprint": fingerprint,
            "fingerprint_version": FINGERPRINT_VERSION,
            "payload": canonical_json_bytes(payload).decode("utf-8"),
            "run_id": run_id,
            "provider_updated_at": provider_updated_at,
            "authority_at": authority.at,
            "authority_kind": authority.kind,
            "lifecycle_state": lifecycle_state,
        },
    ).scalar_one_or_none()
    if inserted is not None:
        return int(inserted), True
    version_id = int(
        connection.execute(
            text(
                f"""
                UPDATE {table} SET
                    last_observed_run_id = :run_id,
                    normalized_payload = CASE
                        WHEN payload_fidelity = 'fingerprint_only'
                            THEN CAST(:payload AS jsonb)
                        ELSE normalized_payload
                    END,
                    payload_fidelity = 'full'
                WHERE {record_column} = :record_id
                  AND fingerprint_version = :fingerprint_version
                  AND content_fingerprint = :fingerprint
                RETURNING id
                """
            ),
            {
                "record_id": record_id,
                "fingerprint": fingerprint,
                "fingerprint_version": FINGERPRINT_VERSION,
                "payload": canonical_json_bytes(payload).decode("utf-8"),
                "run_id": run_id,
            },
        ).scalar_one()
    )
    return version_id, False


def _reconcile_conversation(
    connection: Connection,
    run_id: int,
    parsed: ParsedExport,
    conversation: NormalizedConversation,
) -> tuple[int, Outcome, int, bool]:
    payload = conversation_canonical_payload(conversation)
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
        "title": payload["title"],
        "started_at": payload["started_at"],
        "ended_at": payload["ended_at"],
        "metadata": json.dumps(payload["metadata"]),
        "fingerprint": fingerprint,
        "fingerprint_version": FINGERPRINT_VERSION,
        "provider_updated_at": conversation.provider_updated_at,
        "authority_at": authority.at,
        "authority_kind": authority.kind,
        "run_id": run_id,
        "lifecycle_state": payload["lifecycle_state"],
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
                         canonical_run_id, first_observed_run_id, last_observed_run_id,
                         lifecycle_state, tombstoned_at, tombstone_run_id)
                    VALUES
                        (:source, :namespace, :source_id, :title, :started_at, :ended_at,
                         CAST(:metadata AS jsonb), :fingerprint, :fingerprint_version,
                         CAST(:provider_updated_at AS timestamptz),
                         CAST(:authority_at AS timestamptz), :authority_kind, :run_id,
                         :run_id, :run_id, :lifecycle_state,
                         CASE WHEN :lifecycle_state = 'provider_deleted'
                              THEN CAST(:authority_at AS timestamptz) END,
                         CASE WHEN :lifecycle_state = 'provider_deleted' THEN :run_id END)
                    RETURNING id
                    """
                ),
                params,
            ).scalar_one()
        )
        version_id, version_created = _persist_version(
            connection,
            table="conversation_versions",
            record_column="conversation_id",
            record_id=conversation_id,
            fingerprint=fingerprint,
            payload=payload,
            run_id=run_id,
            provider_updated_at=conversation.provider_updated_at,
            authority=authority,
            lifecycle_state=conversation.lifecycle_state,
        )
        return conversation_id, "inserted", version_id, version_created
    conversation_id = int(existing.id)
    _require_matching_fingerprint_version(
        entity="conversation",
        logical_id=(
            f"{conversation.source}/{conversation.source_record_namespace}/"
            f"{conversation.source_conversation_id}"
        ),
        stored_version=str(existing.fingerprint_version),
    )
    version_id, version_created = _persist_version(
        connection,
        table="conversation_versions",
        record_column="conversation_id",
        record_id=conversation_id,
        fingerprint=fingerprint,
        payload=payload,
        run_id=run_id,
        provider_updated_at=conversation.provider_updated_at,
        authority=authority,
        lifecycle_state=conversation.lifecycle_state,
    )
    if bytes(existing.content_fingerprint) == fingerprint:
        existing_authority = Authority(existing.authority_at, str(existing.authority_kind))
        if authority_advances(existing_authority, authority):
            connection.execute(
                text(
                    """
                    UPDATE conversations SET
                        provider_updated_at = CAST(:provider_updated_at AS timestamptz),
                        authority_at = CAST(:authority_at AS timestamptz),
                        authority_kind = :authority_kind,
                        canonical_run_id = :run_id,
                        last_observed_run_id = :run_id
                    WHERE id = :id
                    """
                ),
                params | {"id": conversation_id},
            )
        else:
            connection.execute(
                text("UPDATE conversations SET last_observed_run_id = :run_id WHERE id = :id"),
                {"run_id": run_id, "id": conversation_id},
            )
        return conversation_id, "unchanged", version_id, version_created
    outcome = classify_changed_authority(
        Authority(existing.authority_at, str(existing.authority_kind)),
        authority,
    )
    if outcome in ("stale", "conflicted"):
        connection.execute(
            text("UPDATE conversations SET last_observed_run_id = :run_id WHERE id = :id"),
            {"run_id": run_id, "id": conversation_id},
        )
        return conversation_id, outcome, version_id, version_created
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
                last_observed_run_id = :run_id,
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
    return (
        conversation_id,
        "tombstoned" if tombstoned else "updated",
        version_id,
        version_created,
    )


def _reconcile_event(
    connection: Connection,
    run_id: int,
    source_file_id: int,
    parsed: ParsedExport,
    conversation: NormalizedConversation | None,
    conversation_id: int | None,
    event: NormalizedEvent,
) -> tuple[Outcome, int, bool]:
    conversation_namespace = conversation.source_record_namespace if conversation else None
    conversation_source_id = conversation.source_conversation_id if conversation else None
    payload = event_canonical_payload(
        event,
        conversation_namespace=conversation_namespace,
        conversation_source_id=conversation_source_id,
    )
    fingerprint = event_fingerprint(
        event,
        conversation_namespace=conversation_namespace,
        conversation_source_id=conversation_source_id,
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
        "occurred_at": payload["occurred_at"],
        "source": event.source,
        "event_type": payload["event_type"],
        "namespace": event.source_record_namespace,
        "actor_identity": payload["actor_identity"],
        "conversation_id": conversation_id,
        "text": payload["text"],
        "metadata": json.dumps(payload["metadata"]),
        "source_id": event.source_record_id,
        "source_node_id": payload["source_node_id"],
        "parent_source_node_id": payload["parent_source_node_id"],
        "fingerprint": fingerprint,
        "fingerprint_version": FINGERPRINT_VERSION,
        "provider_updated_at": event.provider_updated_at,
        "authority_at": authority.at,
        "authority_kind": authority.kind,
        "run_id": run_id,
        "lifecycle_state": payload["lifecycle_state"],
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
                         canonical_run_id, first_observed_run_id, last_observed_run_id,
                         lifecycle_state, tombstoned_at, tombstone_run_id)
                    VALUES
                        (:occurred_at, :source, :event_type, :namespace, NULL,
                         :actor_identity, :conversation_id, :text, CAST(:metadata AS jsonb),
                         :source_id,
                         :source_node_id, :parent_source_node_id, :fingerprint,
                         :fingerprint_version, CAST(:provider_updated_at AS timestamptz),
                         CAST(:authority_at AS timestamptz),
                         :authority_kind, :run_id, :run_id, :run_id, :lifecycle_state,
                         CASE WHEN :lifecycle_state = 'provider_deleted'
                              THEN CAST(:authority_at AS timestamptz) END,
                         CASE WHEN :lifecycle_state = 'provider_deleted' THEN :run_id END)
                    RETURNING id
                    """
                ),
                params,
            ).scalar_one()
        )
        version_id, version_created = _persist_version(
            connection,
            table="event_versions",
            record_column="event_id",
            record_id=event_id,
            fingerprint=fingerprint,
            payload=payload,
            run_id=run_id,
            provider_updated_at=event.provider_updated_at,
            authority=authority,
            lifecycle_state=event.lifecycle_state,
        )
        outcome: Outcome = "inserted"
    else:
        event_id = int(existing.id)
        _require_matching_fingerprint_version(
            entity="event",
            logical_id=(
                f"{event.source}/{event.source_record_namespace}/{event.source_record_id}"
            ),
            stored_version=str(existing.fingerprint_version),
        )
        version_id, version_created = _persist_version(
            connection,
            table="event_versions",
            record_column="event_id",
            record_id=event_id,
            fingerprint=fingerprint,
            payload=payload,
            run_id=run_id,
            provider_updated_at=event.provider_updated_at,
            authority=authority,
            lifecycle_state=event.lifecycle_state,
        )
        if bytes(existing.content_fingerprint) == fingerprint:
            outcome = "unchanged"
            existing_authority = Authority(existing.authority_at, str(existing.authority_kind))
            if authority_advances(existing_authority, authority):
                connection.execute(
                    text(
                        """
                        UPDATE events SET
                            provider_updated_at = CAST(:provider_updated_at AS timestamptz),
                            authority_at = CAST(:authority_at AS timestamptz),
                            authority_kind = :authority_kind,
                            canonical_run_id = :run_id,
                            last_observed_run_id = :run_id
                        WHERE id = :id
                        """
                    ),
                    params | {"id": event_id},
                )
            else:
                connection.execute(
                    text("UPDATE events SET last_observed_run_id = :run_id WHERE id = :id"),
                    {"run_id": run_id, "id": event_id},
                )
        else:
            changed = classify_changed_authority(
                Authority(existing.authority_at, str(existing.authority_kind)),
                authority,
            )
            if changed in ("stale", "conflicted"):
                outcome = changed
                connection.execute(
                    text("UPDATE events SET last_observed_run_id = :run_id WHERE id = :id"),
                    {"run_id": run_id, "id": event_id},
                )
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
                            last_observed_run_id = :run_id,
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
                (import_run_id, source_file_id, event_id, event_version_id, outcome)
            VALUES
                (:run_id, :source_file_id, :event_id, :version_id, :outcome)
            ON CONFLICT DO NOTHING
            """
        ),
        {
            "run_id": run_id,
            "source_file_id": source_file_id,
            "event_id": event_id,
            "version_id": version_id,
            "outcome": outcome,
        },
    )
    return outcome, version_id, version_created


def _reconcile_resource(
    connection: Connection,
    run_id: int,
    source_file_id: int,
    parsed: ParsedExport,
    normalized: NormalizedRecord | NormalizedAsset,
) -> tuple[Outcome, int, bool]:
    """Reconcile records and assets through the same cumulative/versioned contract."""

    if isinstance(normalized, NormalizedRecord):
        entity = "record"
        table = "records"
        version_table = "record_versions"
        observation_table = "record_observations"
        source_link_table = "source_file_records"
        id_column = "record_id"
        version_id_column = "record_version_id"
        namespace_column = "source_record_namespace"
        source_id_column = "source_record_id"
        namespace = normalized.source_record_namespace
        source_id = normalized.source_record_id
        payload = record_canonical_payload(normalized)
        fingerprint = record_fingerprint(normalized)
        canonical_columns = (
            "record_type",
            "title",
            "text",
            "created_at",
            "updated_at",
            "occurred_at",
            "actor_identity",
            "metadata",
        )
        field_values: dict[str, Any] = {
            column: payload[column] for column in canonical_columns
        }
    else:
        entity = "asset"
        table = "assets"
        version_table = "asset_versions"
        observation_table = "asset_observations"
        source_link_table = "source_file_assets"
        id_column = "asset_id"
        version_id_column = "asset_version_id"
        namespace_column = "asset_namespace"
        source_id_column = "source_asset_id"
        namespace = normalized.asset_namespace
        source_id = normalized.source_asset_id
        payload = asset_canonical_payload(normalized)
        fingerprint = asset_fingerprint(normalized)
        canonical_columns = (
            "sha256",
            "mime_type",
            "filename",
            "original_path",
            "size_bytes",
            "created_at",
            "captured_at",
            "modified_at",
            "width",
            "height",
            "duration_ms",
            "metadata",
        )
        field_values = {
            "sha256": normalized.sha256,
            "mime_type": normalized.mime_type,
            "filename": normalized.filename,
            "original_path": normalized.original_path,
            "size_bytes": normalized.size_bytes,
            "created_at": normalized.created_at,
            "captured_at": normalized.captured_at,
            "modified_at": normalized.modified_at,
            "width": normalized.width,
            "height": normalized.height,
            "duration_ms": normalized.duration_ms,
            "metadata": normalized.metadata,
        }

    authority = _incoming_authority(normalized, parsed)
    existing = connection.execute(
        text(
            f"""
            SELECT id, content_fingerprint, fingerprint_version, authority_at,
                   authority_kind, lifecycle_state
            FROM {table}
            WHERE source = :source
              AND {namespace_column} = :namespace
              AND {source_id_column} = :source_id
            """
        ),
        {"source": normalized.source, "namespace": namespace, "source_id": source_id},
    ).one_or_none()
    params: dict[str, Any] = {
        "source": normalized.source,
        "namespace": namespace,
        "source_id": source_id,
        "fingerprint": fingerprint,
        "fingerprint_version": FINGERPRINT_VERSION,
        "provider_updated_at": normalized.provider_updated_at,
        "authority_at": authority.at,
        "authority_kind": authority.kind,
        "run_id": run_id,
        "lifecycle_state": normalized.lifecycle_state,
        **field_values,
    }
    params["metadata"] = json.dumps(field_values["metadata"])
    field_names = ", ".join(canonical_columns)
    field_parameters = ", ".join(
        "CAST(:metadata AS jsonb)" if column == "metadata" else f":{column}"
        for column in canonical_columns
    )
    if existing is None:
        canonical_id = int(
            connection.execute(
                text(
                    f"""
                    INSERT INTO {table}
                        (source, {namespace_column}, {source_id_column}, {field_names},
                         content_fingerprint, fingerprint_version, provider_updated_at,
                         authority_at, authority_kind, canonical_run_id,
                         first_observed_run_id, last_observed_run_id, lifecycle_state,
                         tombstoned_at, tombstone_run_id)
                    VALUES
                        (:source, :namespace, :source_id, {field_parameters},
                         :fingerprint, :fingerprint_version,
                         CAST(:provider_updated_at AS timestamptz),
                         CAST(:authority_at AS timestamptz), :authority_kind, :run_id,
                         :run_id, :run_id, :lifecycle_state,
                         CASE WHEN :lifecycle_state = 'provider_deleted'
                              THEN CAST(:authority_at AS timestamptz) END,
                         CASE WHEN :lifecycle_state = 'provider_deleted' THEN :run_id END)
                    RETURNING id
                    """
                ),
                params,
            ).scalar_one()
        )
        version_id, version_created = _persist_version(
            connection,
            table=version_table,
            record_column=id_column,
            record_id=canonical_id,
            fingerprint=fingerprint,
            payload=payload,
            run_id=run_id,
            provider_updated_at=normalized.provider_updated_at,
            authority=authority,
            lifecycle_state=normalized.lifecycle_state,
        )
        outcome: Outcome = "inserted"
    else:
        canonical_id = int(existing.id)
        _require_matching_fingerprint_version(
            entity=entity,
            logical_id=f"{normalized.source}/{namespace}/{source_id}",
            stored_version=str(existing.fingerprint_version),
        )
        version_id, version_created = _persist_version(
            connection,
            table=version_table,
            record_column=id_column,
            record_id=canonical_id,
            fingerprint=fingerprint,
            payload=payload,
            run_id=run_id,
            provider_updated_at=normalized.provider_updated_at,
            authority=authority,
            lifecycle_state=normalized.lifecycle_state,
        )
        if bytes(existing.content_fingerprint) == fingerprint:
            outcome = "unchanged"
            existing_authority = Authority(existing.authority_at, str(existing.authority_kind))
            authority_updates = ""
            if authority_advances(existing_authority, authority):
                authority_updates = """
                    provider_updated_at = CAST(:provider_updated_at AS timestamptz),
                    authority_at = CAST(:authority_at AS timestamptz),
                    authority_kind = :authority_kind,
                    canonical_run_id = :run_id,
                """
            locator_update = "original_path = :original_path," if entity == "asset" else ""
            connection.execute(
                text(
                    f"""
                    UPDATE {table} SET
                        {authority_updates}
                        {locator_update}
                        last_observed_run_id = :run_id
                    WHERE id = :id
                    """
                ),
                params | {"id": canonical_id},
            )
        else:
            changed = classify_changed_authority(
                Authority(existing.authority_at, str(existing.authority_kind)), authority
            )
            if changed in ("stale", "conflicted"):
                outcome = changed
                connection.execute(
                    text(f"UPDATE {table} SET last_observed_run_id = :run_id WHERE id = :id"),
                    {"run_id": run_id, "id": canonical_id},
                )
            else:
                assignments = ", ".join(
                    f"{column} = CAST(:metadata AS jsonb)"
                    if column == "metadata"
                    else f"{column} = :{column}"
                    for column in canonical_columns
                )
                tombstoned = (
                    str(existing.lifecycle_state) != "provider_deleted"
                    and normalized.lifecycle_state == "provider_deleted"
                )
                connection.execute(
                    text(
                        f"""
                        UPDATE {table} SET
                            {assignments},
                            content_fingerprint = :fingerprint,
                            fingerprint_version = :fingerprint_version,
                            provider_updated_at = CAST(:provider_updated_at AS timestamptz),
                            authority_at = CAST(:authority_at AS timestamptz),
                            authority_kind = :authority_kind,
                            canonical_run_id = :run_id,
                            last_observed_run_id = :run_id,
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
                    params | {"id": canonical_id},
                )
                outcome = "tombstoned" if tombstoned else "updated"

    connection.execute(
        text(
            f"""
            INSERT INTO {source_link_table} (source_file_id, {id_column})
            VALUES (:source_file_id, :canonical_id)
            ON CONFLICT DO NOTHING
            """
        ),
        {"source_file_id": source_file_id, "canonical_id": canonical_id},
    )
    connection.execute(
        text(
            f"""
            INSERT INTO {observation_table}
                (import_run_id, source_file_id, {id_column}, {version_id_column}, outcome)
            VALUES (:run_id, :source_file_id, :canonical_id, :version_id, :outcome)
            ON CONFLICT DO NOTHING
            """
        ),
        {
            "run_id": run_id,
            "source_file_id": source_file_id,
            "canonical_id": canonical_id,
            "version_id": version_id,
            "outcome": outcome,
        },
    )
    return outcome, version_id, version_created


def _empty_counts() -> dict[str, int]:
    return {
        name: 0
        for name in (
            "inserted", "updated", "unchanged", "stale", "conflicted", "tombstoned"
        )
    }


def _import_parsed_export(
    connection: Connection,
    parsed: ParsedExport,
    source_file_id: int,
    run_id: int,
) -> ImportResult:
    conversation_counts = _empty_counts()
    event_counts = _empty_counts()
    new_conversation_versions = 0
    reused_conversation_versions = 0
    new_event_versions = 0
    reused_event_versions = 0
    record_counts = _empty_counts()
    asset_counts = _empty_counts()
    new_record_versions = 0
    reused_record_versions = 0
    new_asset_versions = 0
    reused_asset_versions = 0
    for conversation in parsed.conversations:
        conversation_id, outcome, version_id, version_created = _reconcile_conversation(
            connection, run_id, parsed, conversation
        )
        conversation_counts[outcome] += 1
        new_conversation_versions += int(version_created)
        reused_conversation_versions += int(not version_created)
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
                     conversation_version_id, outcome)
                VALUES
                    (:run_id, :source_file_id, :conversation_id, :version_id, :outcome)
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "run_id": run_id,
                "source_file_id": source_file_id,
                "conversation_id": conversation_id,
                "version_id": version_id,
                "outcome": outcome,
            },
        )
        for event in conversation.events:
            event_outcome, _, version_created = _reconcile_event(
                connection,
                run_id,
                source_file_id,
                parsed,
                conversation,
                conversation_id,
                event,
            )
            event_counts[event_outcome] += 1
            new_event_versions += int(version_created)
            reused_event_versions += int(not version_created)

    for event in parsed.events:
        event_outcome, _, version_created = _reconcile_event(
            connection, run_id, source_file_id, parsed, None, None, event
        )
        event_counts[event_outcome] += 1
        new_event_versions += int(version_created)
        reused_event_versions += int(not version_created)

    for record in parsed.records:
        record_outcome, _, version_created = _reconcile_resource(
            connection, run_id, source_file_id, parsed, record
        )
        record_counts[record_outcome] += 1
        new_record_versions += int(version_created)
        reused_record_versions += int(not version_created)

    for asset in parsed.assets:
        asset_outcome, _, version_created = _reconcile_resource(
            connection, run_id, source_file_id, parsed, asset
        )
        asset_counts[asset_outcome] += 1
        new_asset_versions += int(version_created)
        reused_asset_versions += int(not version_created)

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
    database_records = int(
        connection.execute(
            text(
                """
                SELECT COUNT(*) FROM record_observations
                WHERE import_run_id = :run_id AND source_file_id = :source_file_id
                """
            ),
            {"run_id": run_id, "source_file_id": source_file_id},
        ).scalar_one()
    )
    database_assets = int(
        connection.execute(
            text(
                """
                SELECT COUNT(*) FROM asset_observations
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
        source_records=parsed.record_count,
        database_records=database_records,
        source_assets=parsed.asset_count,
        database_assets=database_assets,
        inserted_conversations=conversation_counts["inserted"],
        updated_conversations=conversation_counts["updated"],
        unchanged_conversations=conversation_counts["unchanged"],
        stale_conversations=conversation_counts["stale"],
        conflicted_conversations=conversation_counts["conflicted"],
        tombstoned_conversations=conversation_counts["tombstoned"],
        new_conversation_versions=new_conversation_versions,
        reused_conversation_versions=reused_conversation_versions,
        inserted_events=event_counts["inserted"],
        updated_events=event_counts["updated"],
        unchanged_events=event_counts["unchanged"],
        stale_events=event_counts["stale"],
        conflicted_events=event_counts["conflicted"],
        tombstoned_events=event_counts["tombstoned"],
        new_event_versions=new_event_versions,
        reused_event_versions=reused_event_versions,
        inserted_records=record_counts["inserted"],
        updated_records=record_counts["updated"],
        unchanged_records=record_counts["unchanged"],
        stale_records=record_counts["stale"],
        conflicted_records=record_counts["conflicted"],
        tombstoned_records=record_counts["tombstoned"],
        new_record_versions=new_record_versions,
        reused_record_versions=reused_record_versions,
        inserted_assets=asset_counts["inserted"],
        updated_assets=asset_counts["updated"],
        unchanged_assets=asset_counts["unchanged"],
        stale_assets=asset_counts["stale"],
        conflicted_assets=asset_counts["conflicted"],
        tombstoned_assets=asset_counts["tombstoned"],
        new_asset_versions=new_asset_versions,
        reused_asset_versions=reused_asset_versions,
    )
    if not result.reconciled:
        raise ReconciliationError(
            "import rolled back: "
            f"conversations {result.database_conversations}/{result.source_conversations}, "
            f"events {result.database_events}/{result.source_events}"
            f", records {result.database_records}/{result.source_records}"
            f", assets {result.database_assets}/{result.source_assets}"
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
        ordered_pairs = tuple(
            sorted(
                zip(parsed_exports, source_file_ids, strict=True),
                key=lambda item: (
                    item[0].sha256,
                    item[0].parser_version,
                    item[0].filename,
                ),
            )
        )
        run_id, status, stats = _create_or_get_run(connection, parsed_exports, run_key)
        _record_run_files(
            connection,
            run_id,
            tuple(source_file_id for _, source_file_id in ordered_pairs),
            tuple(parsed for parsed, _ in ordered_pairs),
        )
        if status in ("completed", "conflicted"):
            stored = stats.get("results")
            if not isinstance(stored, list):
                raise ReconciliationError(f"completed import run {run_id} has no stored results")
            stored_results = tuple(
                ImportResult.model_validate(item).model_copy(update={"short_circuited": True})
                for item in stored
            )
            by_source_file_id = {result.source_file_id: result for result in stored_results}
            return tuple(by_source_file_id[source_file_id] for source_file_id in source_file_ids)
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
            for parsed, source_file_id in ordered_pairs
        )
        conflict_count = sum(
            result.conflicted_conversations
            + result.conflicted_events
            + result.conflicted_records
            + result.conflicted_assets
            for result in results
        )
        final_status = "conflicted" if conflict_count else "completed"
        serialized = [result.model_dump(mode="json") for result in results]
        connection.execute(
            text(
                """
                UPDATE import_runs SET
                    status = :status,
                    completed_at = CURRENT_TIMESTAMP,
                    stats = CAST(:stats AS jsonb),
                    error_summary = :error_summary
                WHERE id = :run_id
                """
            ),
            {
                "run_id": run_id,
                "status": final_status,
                "stats": json.dumps(
                    {"results": serialized, "conflicted_records": conflict_count}
                ),
                "error_summary": (
                    f"{conflict_count} ambiguous record conflict(s); canonical rows unchanged"
                    if conflict_count
                    else None
                ),
            },
        )
        by_source_file_id = {result.source_file_id: result for result in results}
        return tuple(by_source_file_id[source_file_id] for source_file_id in source_file_ids)


def import_openai_export(engine: Engine, parsed: ParsedExport) -> ImportResult:
    if parsed.source != "openai":
        raise ValueError("import_openai_export requires an OpenAI parsed export")
    return import_parsed_exports(engine, (parsed,))[0]
