from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from .models import ImportResult, ParsedExport
from .openai_importer import PARSER_VERSION, SOURCE


class ReconciliationError(RuntimeError):
    """Raised when source and database counts differ."""


def create_database_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


@contextmanager
def _transaction(engine: Engine) -> Iterator[Connection]:
    with engine.begin() as connection:
        yield connection


def _upsert_source_file(connection: Connection, parsed: ParsedExport) -> int:
    return int(
        connection.execute(
            text(
                """
                INSERT INTO source_files
                    (source, filename, original_path, sha256, size_bytes, parser_version)
                VALUES
                    (:source, :filename, :original_path, :sha256, :size_bytes, :parser_version)
                ON CONFLICT (source, sha256) DO UPDATE SET
                    filename = EXCLUDED.filename,
                    original_path = EXCLUDED.original_path,
                    size_bytes = EXCLUDED.size_bytes,
                    parser_version = EXCLUDED.parser_version,
                    ingested_at = CURRENT_TIMESTAMP
                RETURNING id
                """
            ),
            {
                "source": SOURCE,
                "filename": parsed.filename,
                "original_path": parsed.original_path,
                "sha256": parsed.sha256,
                "size_bytes": parsed.size_bytes,
                "parser_version": PARSER_VERSION,
            },
        ).scalar_one()
    )


def _insert_source_file_conversation(
    connection: Connection,
    source_file_id: int,
    conversation_id: int,
) -> None:
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


def _upsert_event(
    connection: Connection,
    conversation_id: int,
    event: Any,
) -> int:
    import json

    return int(
        connection.execute(
            text(
                """
                INSERT INTO events
                    (occurred_at, source, event_type, actor_person_id, conversation_id, text,
                     metadata, source_record_id, source_node_id, parent_source_node_id)
                VALUES
                    (:occurred_at, :source, :event_type, NULL, :conversation_id, :text,
                     CAST(:metadata AS jsonb), :source_record_id, :source_node_id,
                     :parent_source_node_id)
                ON CONFLICT (source, source_record_id) DO UPDATE SET
                    occurred_at = EXCLUDED.occurred_at,
                    event_type = EXCLUDED.event_type,
                    actor_person_id = COALESCE(EXCLUDED.actor_person_id, events.actor_person_id),
                    conversation_id = EXCLUDED.conversation_id,
                    text = EXCLUDED.text,
                    metadata = EXCLUDED.metadata,
                    source_node_id = EXCLUDED.source_node_id,
                    parent_source_node_id = EXCLUDED.parent_source_node_id
                RETURNING id
                """
            ),
            {
                "occurred_at": event.occurred_at,
                "source": event.source,
                "event_type": event.event_type,
                "conversation_id": conversation_id,
                "text": event.text,
                "metadata": json.dumps(event.metadata),
                "source_record_id": event.source_record_id,
                "source_node_id": event.source_node_id,
                "parent_source_node_id": event.parent_source_node_id,
            },
        ).scalar_one()
    )


def _insert_source_file_event(
    connection: Connection,
    source_file_id: int,
    event_id: int,
) -> None:
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


def import_openai_export(engine: Engine, parsed: ParsedExport) -> ImportResult:
    import json

    with _transaction(engine) as connection:
        source_file_id = _upsert_source_file(connection, parsed)
        for conversation in parsed.conversations:
            # Bind JSON explicitly so the implementation is independent of a
            # SQLAlchemy declarative model named `metadata`.
            params = conversation.model_dump()
            params["metadata"] = json.dumps(params["metadata"])
            conversation_id = int(
                connection.execute(
                    text(
                        """
                        INSERT INTO conversations
                            (source, source_conversation_id, title, started_at, ended_at, metadata)
                        VALUES
                            (:source, :source_conversation_id, :title, :started_at, :ended_at,
                             CAST(:metadata AS jsonb))
                        ON CONFLICT (source, source_conversation_id) DO UPDATE SET
                            title = EXCLUDED.title,
                            started_at = EXCLUDED.started_at,
                            ended_at = EXCLUDED.ended_at,
                            metadata = EXCLUDED.metadata
                        RETURNING id
                        """
                    ),
                    params,
                ).scalar_one()
            )
            _insert_source_file_conversation(connection, source_file_id, conversation_id)
            for event in conversation.events:
                event_id = _upsert_event(connection, conversation_id, event)
                _insert_source_file_event(connection, source_file_id, event_id)

        database_events = int(
            connection.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM source_file_events
                    WHERE source_file_id = :source_file_id
                    """
                ),
                {"source_file_id": source_file_id},
            ).scalar_one()
        )
        database_conversations = int(
            connection.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM source_file_conversations
                    WHERE source_file_id = :source_file_id
                    """
                ),
                {"source_file_id": source_file_id},
            ).scalar_one()
        )
        result = ImportResult(
            source_file_id=source_file_id,
            source_conversations=parsed.conversation_count,
            database_conversations=database_conversations,
            source_events=parsed.event_count,
            database_events=database_events,
        )
        if not result.reconciled:
            raise ReconciliationError(
                "import rolled back: "
                f"conversations {result.database_conversations}/{result.source_conversations}, "
                f"events {result.database_events}/{result.source_events}"
            )
        return result
