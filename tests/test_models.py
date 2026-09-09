import pytest
from pydantic import ValidationError

from personal_data_lab.models import (
    ImportResult,
    NormalizedConversation,
    NormalizedEvent,
    ParsedExport,
)


def test_reconciliation_requires_both_counts() -> None:
    assert ImportResult(
        source_file_id=1,
        source_conversations=2,
        database_conversations=2,
        source_events=3,
        database_events=3,
    ).reconciled
    assert not ImportResult(
        source_file_id=1,
        source_conversations=2,
        database_conversations=2,
        source_events=3,
        database_events=2,
    ).reconciled


def test_parsed_export_requires_consistent_sources() -> None:
    with pytest.raises(ValidationError, match="event source must match"):
        ParsedExport(
            source="anthropic",
            parser_version="anthropic-test-v1",
            filename="conversations-000.zip",
            original_path="synthetic://conversations-000.zip",
            sha256="d" * 64,
            size_bytes=1,
            conversations=(
                NormalizedConversation(
                    source="anthropic",
                    source_conversation_id="conversation-1",
                    events=(
                        NormalizedEvent(
                            occurred_at=None,
                            source="openai",
                            event_type="conversation_message",
                            source_record_id="message-1",
                        ),
                    ),
                ),
            ),
        )
