from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class Authority:
    """Provider chronology evidence selected by an adapter/import policy."""

    at: datetime | None
    kind: str


def classify_changed_authority(
    existing: Authority,
    incoming: Authority,
) -> Literal["updated", "stale", "conflicted"]:
    """Choose safely between different payloads without using ingestion order."""

    if existing.kind == "unknown" and incoming.kind != "unknown":
        return "updated"
    if existing.kind != incoming.kind:
        return "conflicted"
    if existing.at is None or incoming.at is None:
        return "conflicted"
    if incoming.at > existing.at:
        return "updated"
    if incoming.at < existing.at:
        return "stale"
    return "conflicted"


def authority_advances(existing: Authority, incoming: Authority) -> bool:
    """Whether same-content evidence safely advances the canonical watermark."""

    if incoming.at is None or incoming.kind == "unknown":
        return False
    if existing.kind == "unknown" or existing.at is None:
        return True
    return existing.kind == incoming.kind and incoming.at > existing.at
