from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NormalizedEvent(BaseModel):
    """Source-independent event produced by an importer."""

    model_config = ConfigDict(frozen=True)

    occurred_at: datetime | None
    source: str
    event_type: str
    actor_identity: str | None = None
    text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_record_id: str
    source_node_id: str | None = None
    parent_source_node_id: str | None = None


class NormalizedConversation(BaseModel):
    """Conversation plus its normalized events."""

    model_config = ConfigDict(frozen=True)

    source: str
    source_conversation_id: str
    title: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    events: tuple[NormalizedEvent, ...] = ()


class ParsedExport(BaseModel):
    """Normalized content and immutable-source provenance."""

    model_config = ConfigDict(frozen=True)

    filename: str
    original_path: str
    sha256: str
    size_bytes: int
    conversations: tuple[NormalizedConversation, ...]

    @property
    def conversation_count(self) -> int:
        return len(self.conversations)

    @property
    def event_count(self) -> int:
        return sum(len(conversation.events) for conversation in self.conversations)


class ImportResult(BaseModel):
    """Import counts; database counts are memberships scoped to this source file."""

    model_config = ConfigDict(frozen=True)

    source_file_id: int
    source_conversations: int
    database_conversations: int
    source_events: int
    database_events: int

    @property
    def reconciled(self) -> bool:
        return (
            self.source_conversations == self.database_conversations
            and self.source_events == self.database_events
        )
