from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from .models import NormalizedConversation, NormalizedEvent

FINGERPRINT_VERSION = "canonical-v1"


class FingerprintError(ValueError):
    """Raised when a normalized value cannot be serialized deterministically."""


def _normalized_json_value(value: Any) -> Any:
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
            key: _normalized_json_value(item)
            for key, item in sorted(value.items())
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalized_json_value(item) for item in value]
    raise FingerprintError(f"unsupported fingerprint value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize the project's canonical JSON subset as deterministic UTF-8."""

    try:
        return json.dumps(
            _normalized_json_value(value),
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


def conversation_fingerprint(conversation: NormalizedConversation) -> bytes:
    """Fingerprint only fields persisted on the canonical conversation row."""

    return fingerprint_value(
        {
            "title": conversation.title,
            "started_at": conversation.started_at,
            "ended_at": conversation.ended_at,
            "metadata": conversation.metadata,
            "lifecycle_state": conversation.lifecycle_state,
        }
    )


def event_fingerprint(
    event: NormalizedEvent,
    *,
    conversation_namespace: str | None = None,
    conversation_source_id: str | None = None,
) -> bytes:
    """Fingerprint only fields persisted on the canonical event row."""

    return fingerprint_value(
        {
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
    )

