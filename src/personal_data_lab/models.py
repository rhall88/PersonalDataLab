from __future__ import annotations

from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


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

    source: str
    parser_version: str
    filename: str
    original_path: str
    sha256: str
    size_bytes: int
    exported_at: datetime | None = None
    conversations: tuple[NormalizedConversation, ...] = ()
    events: tuple[NormalizedEvent, ...] = ()

    @model_validator(mode="after")
    def require_one_source(self) -> Self:
        for conversation in self.conversations:
            if conversation.source != self.source:
                raise ValueError("conversation source must match parsed export source")
            if any(event.source != self.source for event in conversation.events):
                raise ValueError("conversation event source must match parsed export source")
        if any(event.source != self.source for event in self.events):
            raise ValueError("standalone event source must match parsed export source")
        return self

    @property
    def conversation_count(self) -> int:
        return len(self.conversations)

    @property
    def event_count(self) -> int:
        return len(self.events) + sum(
            len(conversation.events) for conversation in self.conversations
        )


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
