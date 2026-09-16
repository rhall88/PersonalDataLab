from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from .models import NormalizedAsset, NormalizedConversation, NormalizedEvent, NormalizedRecord

FINGERPRINT_VERSION = "canonical-v1"


class FingerprintError(ValueError):
    """Raised when a normalized value cannot be serialized deterministically."""


def canonical_json_value(value: Any) -> Any:
    """Return the JSON-safe value used by both hashes and stored version payloads."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FingerprintError("non-finite numbers cannot be fingerprinted")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise FingerprintError("naive datetimes cannot be fingerprinted")
        normalized = value.astimezone(UTC)
        return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, (Path, UUID)):
        return str(value)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise FingerprintError("JSON object keys must be strings")
        return {
            key: canonical_json_value(item)
            for key, item in sorted(value.items())
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [canonical_json_value(item) for item in value]
    raise FingerprintError(f"unsupported fingerprint value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize the project's canonical JSON subset as deterministic UTF-8."""

    try:
        return json.dumps(
            canonical_json_value(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        if isinstance(error, FingerprintError):
            raise
        raise FingerprintError(str(error)) from error


def fingerprint_value(value: Any) -> bytes:
    return hashlib.sha256(canonical_json_bytes(value)).digest()


def conversation_canonical_payload(conversation: NormalizedConversation) -> dict[str, Any]:
    """Define the complete normalized content of a canonical conversation."""

    return {
        "title": conversation.title,
        "started_at": conversation.started_at,
        "ended_at": conversation.ended_at,
        "metadata": conversation.metadata,
        "lifecycle_state": conversation.lifecycle_state,
    }


def conversation_fingerprint(conversation: NormalizedConversation) -> bytes:
    return fingerprint_value(conversation_canonical_payload(conversation))


def event_canonical_payload(
    event: NormalizedEvent,
    *,
    conversation_namespace: str | None = None,
    conversation_source_id: str | None = None,
) -> dict[str, Any]:
    """Define the complete normalized content of a canonical event."""

    return {
        "occurred_at": event.occurred_at,
        "event_type": event.event_type,
        "actor_identity": event.actor_identity,
        "conversation_namespace": conversation_namespace,
        "conversation_source_id": conversation_source_id,
        "text": event.text,
        "metadata": event.metadata,
        "source_node_id": event.source_node_id,
        "parent_source_node_id": event.parent_source_node_id,
        "lifecycle_state": event.lifecycle_state,
    }


def event_fingerprint(
    event: NormalizedEvent,
    *,
    conversation_namespace: str | None = None,
    conversation_source_id: str | None = None,
) -> bytes:
    return fingerprint_value(
        event_canonical_payload(
            event,
            conversation_namespace=conversation_namespace,
            conversation_source_id=conversation_source_id,
        )
    )


def record_canonical_payload(record: NormalizedRecord) -> dict[str, Any]:
    """Define the complete normalized content of a canonical durable record."""

    return {
        "record_type": record.record_type,
        "title": record.title,
        "text": record.text,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "occurred_at": record.occurred_at,
        "actor_identity": record.actor_identity,
        "metadata": record.metadata,
        "lifecycle_state": record.lifecycle_state,
    }


def record_fingerprint(record: NormalizedRecord) -> bytes:
    return fingerprint_value(record_canonical_payload(record))


def asset_canonical_payload(asset: NormalizedAsset) -> dict[str, Any]:
    """Define asset metadata without copying the referenced content bytes."""

    return {
        "sha256": asset.sha256,
        "mime_type": asset.mime_type,
        "filename": asset.filename,
        "size_bytes": asset.size_bytes,
        "created_at": asset.created_at,
        "captured_at": asset.captured_at,
        "modified_at": asset.modified_at,
        "width": asset.width,
        "height": asset.height,
        "duration_ms": asset.duration_ms,
        "metadata": asset.metadata,
        "lifecycle_state": asset.lifecycle_state,
    }


def asset_fingerprint(asset: NormalizedAsset) -> bytes:
    return fingerprint_value(asset_canonical_payload(asset))
