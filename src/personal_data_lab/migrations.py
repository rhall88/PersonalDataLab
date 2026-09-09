from __future__ import annotations

import hashlib
import json
import sysconfig
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .fingerprints import (
    FINGERPRINT_VERSION,
    conversation_fingerprint,
    event_fingerprint,
    fingerprint_value,
)
from .models import NormalizedConversation, NormalizedEvent

LATEST_SCHEMA_VERSION = 4
MIGRATIONS_DIRECTORIES = (
    Path(__file__).resolve().parents[2] / "db" / "migrations",
    Path(sysconfig.get_path("data")) / "share" / "personal-data-lab" / "migrations",
)


class MigrationError(RuntimeError):
    """Raised when the database cannot be upgraded safely."""


def _ensure_migration_table(connection: Connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    has_canonical_tables = bool(
        connection.execute(
            text("SELECT to_regclass('conversations') IS NOT NULL")
        ).scalar_one()
    )
    versions = connection.execute(
        text("SELECT version FROM schema_migrations ORDER BY version")
    ).scalars().all()
    if has_canonical_tables and not versions:
        connection.execute(text("INSERT INTO schema_migrations (version) VALUES (2)"))


def _read_migration(version: int) -> str:
    candidates = next(
        (
            matches
            for directory in MIGRATIONS_DIRECTORIES
            if (matches := sorted(directory.glob(f"{version:03d}_*.sql")))
        ),
        [],
    )
    if len(candidates) != 1:
        raise MigrationError(
            f"expected exactly one SQL migration for version {version}; found {len(candidates)}"
        )
    return candidates[0].read_text(encoding="utf-8")


def _backfill_fingerprints(connection: Connection) -> None:
    conversations = connection.execute(
        text(
            """
            SELECT id, source, source_record_namespace, source_conversation_id, title,
                   started_at, ended_at, metadata, lifecycle_state
            FROM conversations
            ORDER BY id
            """
        )
    ).mappings()
    for row in conversations:
        normalized = NormalizedConversation(
            source=row["source"],
            source_record_namespace=row["source_record_namespace"],
            source_conversation_id=row["source_conversation_id"],
            title=row["title"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            metadata=dict(row["metadata"]),
            lifecycle_state=row["lifecycle_state"],
        )
        connection.execute(
            text(
                """
                UPDATE conversations
                SET content_fingerprint = :fingerprint,
                    fingerprint_version = :version
                WHERE id = :id
                """
            ),
            {
                "id": row["id"],
                "fingerprint": conversation_fingerprint(normalized),
                "version": FINGERPRINT_VERSION,
            },
        )

    events = connection.execute(
        text(
            """
            SELECT e.id, e.occurred_at, e.source, e.event_type,
                   e.source_record_namespace, e.actor_identity, e.text, e.metadata,
                   e.source_record_id, e.source_node_id, e.parent_source_node_id,
                   e.lifecycle_state, c.source_record_namespace AS conversation_namespace,
                   c.source_conversation_id
            FROM events e
            LEFT JOIN conversations c ON c.id = e.conversation_id
            ORDER BY e.id
            """
        )
    ).mappings()
    for row in events:
        normalized = NormalizedEvent(
            occurred_at=row["occurred_at"],
            source=row["source"],
            event_type=row["event_type"],
            source_record_namespace=row["source_record_namespace"],
            actor_identity=row["actor_identity"],
            text=row["text"],
            metadata=dict(row["metadata"]),
            source_record_id=row["source_record_id"],
            source_node_id=row["source_node_id"],
            parent_source_node_id=row["parent_source_node_id"],
            lifecycle_state=row["lifecycle_state"],
        )
        connection.execute(
            text(
                """
                UPDATE events
                SET content_fingerprint = :fingerprint,
                    fingerprint_version = :version
                WHERE id = :id
                """
            ),
            {
                "id": row["id"],
                "fingerprint": event_fingerprint(
                    normalized,
                    conversation_namespace=row["conversation_namespace"],
                    conversation_source_id=row["source_conversation_id"],
                ),
                "version": FINGERPRINT_VERSION,
            },
        )

    attachments = connection.execute(
        text(
            """
            SELECT id, event_id, source, mime_type, path, sha256, file_size, width,
                   height, duration, metadata, lifecycle_state
            FROM attachments
            ORDER BY id
            """
        )
    ).mappings()
    for row in attachments:
        projection = {
            key: row[key]
            for key in (
                "event_id",
                "mime_type",
                "path",
                "sha256",
                "file_size",
                "width",
                "height",
                "duration",
                "metadata",
                "lifecycle_state",
            )
        }
        connection.execute(
            text(
                """
                UPDATE attachments
                SET content_fingerprint = :fingerprint,
                    fingerprint_version = :version
                WHERE id = :id
                """
            ),
            {
                "id": row["id"],
                "fingerprint": fingerprint_value(projection),
                "version": FINGERPRINT_VERSION,
            },
        )


def _backfill_legacy_observations(connection: Connection) -> None:
    source_files = connection.execute(
        text(
            """
            SELECT id, source, sha256, parser_version, exported_at, ingested_at
            FROM source_files
            ORDER BY id
            """
        )
    ).mappings()
    for source_file in source_files:
        run_key = hashlib.sha256(
            f"legacy-membership:{source_file['source']}:{source_file['sha256']}".encode()
        ).digest()
        stats = json.dumps({"provenance_fidelity": "membership_only"})
        run_id = int(
            connection.execute(
                text(
                    """
                    INSERT INTO import_runs
                        (source, run_key, snapshot_kind, snapshot_scope, exported_at,
                         parser_version, fingerprint_version, status, started_at,
                         completed_at, stats)
                    VALUES
                        (:source, :run_key, 'unknown', 'legacy', :exported_at,
                         :parser_version, :fingerprint_version, 'completed', :ingested_at,
                         :ingested_at, CAST(:stats AS jsonb))
                    RETURNING id
                    """
                ),
                {
                    "source": source_file["source"],
                    "run_key": run_key,
                    "exported_at": source_file["exported_at"],
                    "parser_version": source_file["parser_version"],
                    "fingerprint_version": FINGERPRINT_VERSION,
                    "ingested_at": source_file["ingested_at"],
                    "stats": stats,
                },
            ).scalar_one()
        )
        connection.execute(
            text(
                """
                INSERT INTO import_run_source_files
                    (import_run_id, source_file_id, category, ordinal)
                VALUES (:run_id, :source_file_id, 'legacy', 0)
                """
            ),
            {"run_id": run_id, "source_file_id": source_file["id"]},
        )
        connection.execute(
            text(
                """
                INSERT INTO conversation_observations
                    (import_run_id, source_file_id, conversation_id,
                     observed_content_fingerprint, outcome)
                SELECT :run_id, sfc.source_file_id, sfc.conversation_id,
                       c.content_fingerprint, 'unchanged'
                FROM source_file_conversations sfc
                JOIN conversations c ON c.id = sfc.conversation_id
                WHERE sfc.source_file_id = :source_file_id
                """
            ),
            {"run_id": run_id, "source_file_id": source_file["id"]},
        )
        connection.execute(
            text(
                """
                INSERT INTO event_observations
                    (import_run_id, source_file_id, event_id,
                     observed_content_fingerprint, outcome)
                SELECT :run_id, sfe.source_file_id, sfe.event_id,
                       e.content_fingerprint, 'unchanged'
                FROM source_file_events sfe
                JOIN events e ON e.id = sfe.event_id
                WHERE sfe.source_file_id = :source_file_id
                """
            ),
            {"run_id": run_id, "source_file_id": source_file["id"]},
        )


def migrate_database(engine: Engine) -> tuple[int, ...]:
    """Upgrade a PR2 database to the latest cumulative-canonical schema."""

    applied: list[int] = []
    with engine.begin() as connection:
        _ensure_migration_table(connection)
        current = set(
            connection.execute(text("SELECT version FROM schema_migrations")).scalars()
        )
        if any(version > LATEST_SCHEMA_VERSION for version in current):
            raise MigrationError("database schema is newer than this importer")
        for version in range(3, LATEST_SCHEMA_VERSION + 1):
            if version in current:
                continue
            connection.exec_driver_sql(_read_migration(version))
            if version == 3:
                _backfill_fingerprints(connection)
                _backfill_legacy_observations(connection)
            connection.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                {"version": version},
            )
            applied.append(version)
    return tuple(applied)
