import pytest
from pydantic import ValidationError

from personal_data_lab.models import (
    ImportResult,
    NormalizedAsset,
    NormalizedConversation,
    NormalizedEvent,
    NormalizedRecord,
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
        source_conversations=0,
        database_conversations=0,
        source_events=0,
        database_events=0,
        source_records=1,
        database_records=0,
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


def test_parsed_export_counts_and_validates_records_and_assets() -> None:
    parsed = ParsedExport(
        source="google",
        parser_version="test-v1",
        filename="member.json",
        original_path="synthetic://member.json",
        sha256="a" * 64,
        size_bytes=1,
        records=(
            NormalizedRecord(
                source="google",
                record_type="contact",
                source_record_namespace="contacts",
                source_record_id="1",
            ),
        ),
        assets=(
            NormalizedAsset(
                source="google",
                asset_namespace="photos",
                source_asset_id="1",
                filename="photo.jpg",
                original_path="C:/takeout.zip!/Takeout/Google Photos/photo.jpg",
                size_bytes=10,
            ),
        ),
    )
    assert parsed.record_count == 1
    assert parsed.asset_count == 1

    with pytest.raises(ValidationError, match="record source must match"):
        ParsedExport.model_validate(
            parsed.model_dump()
            | {
                "records": [
                    parsed.records[0].model_dump() | {"source": "anthropic"}
                ]
            }
        )
