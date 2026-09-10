from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class NormalizedEvent(BaseModel):
    """Source-independent event produced by an importer."""

    model_config = ConfigDict(frozen=True)

    occurred_at: datetime | None
    source: str
    event_type: str
    source_record_namespace: str = "event"
    actor_identity: str | None = None
    text: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_record_id: str
    source_node_id: str | None = None
    parent_source_node_id: str | None = None
    provider_updated_at: datetime | None = None
    authority_at: datetime | None = None
    authority_kind: Literal[
        "provider_revision", "provider_updated_at", "exported_at", "unknown"
    ] = "unknown"
    lifecycle_state: Literal["active", "provider_deleted"] = "active"


class NormalizedConversation(BaseModel):
    """Conversation plus its normalized events."""

    model_config = ConfigDict(frozen=True)

    source: str
    source_conversation_id: str
    source_record_namespace: str = "conversation"
    title: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    events: tuple[NormalizedEvent, ...] = ()
    provider_updated_at: datetime | None = None
    authority_at: datetime | None = None
    authority_kind: Literal[
        "provider_revision", "provider_updated_at", "exported_at", "unknown"
    ] = "unknown"
    lifecycle_state: Literal["active", "provider_deleted"] = "active"


class ParsedExport(BaseModel):
    """Normalized content and immutable-source provenance."""

    model_config = ConfigDict(frozen=True)

    source: str
    parser_version: str
    category: str | None = None
    filename: str
    original_path: str
    sha256: str
    size_bytes: int
    exported_at: datetime | None = None
    export_id: str | None = None
    snapshot_kind: Literal["unknown", "partial", "complete"] = "unknown"
    snapshot_scope: str = "provider"
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
    run_id: int | None = None
    inserted_conversations: int = 0
    updated_conversations: int = 0
    unchanged_conversations: int = 0
    stale_conversations: int = 0
    conflicted_conversations: int = 0
    tombstoned_conversations: int = 0
    new_conversation_versions: int = 0
    reused_conversation_versions: int = 0
    inserted_events: int = 0
    updated_events: int = 0
    unchanged_events: int = 0
    stale_events: int = 0
    conflicted_events: int = 0
    tombstoned_events: int = 0
    new_event_versions: int = 0
    reused_event_versions: int = 0
    short_circuited: bool = False

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicted_conversations or self.conflicted_events)

    @computed_field
    @property
    def new_distinct_versions(self) -> int:
        return self.new_conversation_versions + self.new_event_versions

    @computed_field
    @property
    def reused_versions(self) -> int:
        return self.reused_conversation_versions + self.reused_event_versions

    @property
    def reconciled(self) -> bool:
        return (
            self.source_conversations == self.database_conversations
            and self.source_events == self.database_events
        )
