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


class NormalizedRecord(BaseModel):
    """Durable provider object whose identity is independent of one event."""

    model_config = ConfigDict(frozen=True)

    source: str
    record_type: str
    source_record_namespace: str = "record"
    source_record_id: str
    title: str | None = None
    text: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    occurred_at: datetime | None = None
    actor_identity: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    provider_updated_at: datetime | None = None
    authority_at: datetime | None = None
    authority_kind: Literal[
        "provider_revision", "provider_updated_at", "exported_at", "unknown"
    ] = "unknown"
    lifecycle_state: Literal["active", "provider_deleted"] = "active"


class NormalizedAsset(BaseModel):
    """External binary/resource reference; content bytes are never stored here."""

    model_config = ConfigDict(frozen=True)

    source: str
    asset_namespace: str
    source_asset_id: str
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mime_type: str | None = None
    filename: str
    original_path: str
    size_bytes: int = Field(ge=0)
    created_at: datetime | None = None
    captured_at: datetime | None = None
    modified_at: datetime | None = None
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    provider_updated_at: datetime | None = None
    authority_at: datetime | None = None
    authority_kind: Literal[
        "provider_revision", "provider_updated_at", "exported_at", "unknown"
    ] = "unknown"
    lifecycle_state: Literal["active", "provider_deleted"] = "active"


class NormalizedRelationship(BaseModel):
    """Explicit provider-evidenced edge between two durable normalized objects."""

    model_config = ConfigDict(frozen=True)

    source: str
    relationship_type: str
    source_relationship_namespace: str = "relationship"
    source_relationship_id: str
    subject_kind: Literal["conversation", "event", "record", "asset"]
    subject_source: str
    subject_namespace: str
    subject_source_id: str
    object_kind: Literal["conversation", "event", "record", "asset"]
    object_source: str
    object_namespace: str
    object_source_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
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
    parse_warnings: tuple[str, ...] = ()
    conversations: tuple[NormalizedConversation, ...] = ()
    events: tuple[NormalizedEvent, ...] = ()
    records: tuple[NormalizedRecord, ...] = ()
    assets: tuple[NormalizedAsset, ...] = ()
    relationships: tuple[NormalizedRelationship, ...] = ()

    @model_validator(mode="after")
    def require_one_source(self) -> Self:
        for conversation in self.conversations:
            if conversation.source != self.source:
                raise ValueError("conversation source must match parsed export source")
            if any(event.source != self.source for event in conversation.events):
                raise ValueError("conversation event source must match parsed export source")
        if any(event.source != self.source for event in self.events):
            raise ValueError("standalone event source must match parsed export source")
        if any(record.source != self.source for record in self.records):
            raise ValueError("record source must match parsed export source")
        if any(asset.source != self.source for asset in self.assets):
            raise ValueError("asset source must match parsed export source")
        if any(relationship.source != self.source for relationship in self.relationships):
            raise ValueError("relationship source must match parsed export source")
        return self

    @property
    def conversation_count(self) -> int:
        return len(self.conversations)

    @property
    def event_count(self) -> int:
        return len(self.events) + sum(
            len(conversation.events) for conversation in self.conversations
        )

    @property
    def record_count(self) -> int:
        return len(self.records)

    @property
    def asset_count(self) -> int:
        return len(self.assets)

    @property
    def relationship_count(self) -> int:
        return len(self.relationships)


class ImportResult(BaseModel):
    """Import counts; database counts are memberships scoped to this source file."""

    model_config = ConfigDict(frozen=True)

    source_file_id: int
    source_conversations: int
    database_conversations: int
    source_events: int
    database_events: int
    source_records: int = 0
    database_records: int = 0
    source_assets: int = 0
    database_assets: int = 0
    source_relationships: int = 0
    database_relationships: int = 0
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
    inserted_records: int = 0
    updated_records: int = 0
    unchanged_records: int = 0
    stale_records: int = 0
    conflicted_records: int = 0
    tombstoned_records: int = 0
    new_record_versions: int = 0
    reused_record_versions: int = 0
    inserted_assets: int = 0
    updated_assets: int = 0
    unchanged_assets: int = 0
    stale_assets: int = 0
    conflicted_assets: int = 0
    tombstoned_assets: int = 0
    new_asset_versions: int = 0
    reused_asset_versions: int = 0
    inserted_relationships: int = 0
    updated_relationships: int = 0
    unchanged_relationships: int = 0
    stale_relationships: int = 0
    conflicted_relationships: int = 0
    tombstoned_relationships: int = 0
    new_relationship_versions: int = 0
    reused_relationship_versions: int = 0
    short_circuited: bool = False

    @property
    def has_conflicts(self) -> bool:
        return bool(
            self.conflicted_conversations
            or self.conflicted_events
            or self.conflicted_records
            or self.conflicted_assets
            or self.conflicted_relationships
        )

    @computed_field
    @property
    def new_distinct_versions(self) -> int:
        return (
            self.new_conversation_versions
            + self.new_event_versions
            + self.new_record_versions
            + self.new_asset_versions
            + self.new_relationship_versions
        )

    @computed_field
    @property
    def reused_versions(self) -> int:
        return (
            self.reused_conversation_versions
            + self.reused_event_versions
            + self.reused_record_versions
            + self.reused_asset_versions
            + self.reused_relationship_versions
        )

    @property
    def reconciled(self) -> bool:
        return (
            self.source_conversations == self.database_conversations
            and self.source_events == self.database_events
            and self.source_records == self.database_records
            and self.source_assets == self.database_assets
            and self.source_relationships == self.database_relationships
        )
