from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from personal_data_lab.fingerprints import (
    FingerprintError,
    canonical_json_bytes,
    event_fingerprint,
    fingerprint_value,
)
from personal_data_lab.models import NormalizedEvent


def test_canonical_json_is_order_independent_and_utf8() -> None:
    first = {"z": "café", "a": {"second": 2, "first": 1}}
    second = {"a": {"first": 1, "second": 2}, "z": "café"}

    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert canonical_json_bytes(first) == (
        b'{"a":{"first":1,"second":2},"z":"caf\xc3\xa9"}'
    )


def test_equivalent_instants_have_the_same_fingerprint() -> None:
    utc = datetime(2026, 9, 9, 12, 30, tzinfo=UTC)
    offset = utc.astimezone(timezone(timedelta(hours=-4)))

    assert fingerprint_value({"at": utc}) == fingerprint_value({"at": offset})


def test_non_finite_numbers_and_naive_datetimes_are_rejected() -> None:
    with pytest.raises(FingerprintError, match="non-finite"):
        fingerprint_value(float("nan"))
    with pytest.raises(FingerprintError, match="naive"):
        fingerprint_value(datetime.fromisoformat("2026-09-09T00:00:00"))


def test_provenance_fields_do_not_affect_event_fingerprint() -> None:
    base = NormalizedEvent(
        occurred_at=datetime(2026, 9, 9, tzinfo=UTC),
        source="openai",
        event_type="conversation_message",
        source_record_namespace="message",
        source_record_id="message-1",
        text="hello",
    )
    observed_later = base.model_copy(
        update={
            "provider_updated_at": datetime(2026, 9, 10, tzinfo=UTC),
            "authority_at": datetime(2026, 9, 10, tzinfo=UTC),
            "authority_kind": "provider_updated_at",
        }
    )

    assert event_fingerprint(base) == event_fingerprint(observed_later)
    assert event_fingerprint(base.model_copy(update={"text": "edited"})) != event_fingerprint(
        base
    )
