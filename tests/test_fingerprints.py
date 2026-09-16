from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from personal_data_lab.fingerprints import (
    FingerprintError,
    asset_fingerprint,
    canonical_json_bytes,
    event_canonical_payload,
    event_fingerprint,
    fingerprint_value,
    record_fingerprint,
)
from personal_data_lab.models import NormalizedAsset, NormalizedEvent, NormalizedRecord


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


def test_event_fingerprint_uses_the_stored_canonical_payload_definition() -> None:
    event = NormalizedEvent(
        occurred_at=datetime(2026, 9, 9, tzinfo=UTC),
        source="openai",
        event_type="conversation_message",
        source_record_namespace="message",
        source_record_id="message-1",
        text="shared definition",
        metadata={"nested": {"answer": 42}},
    )
    payload = event_canonical_payload(
        event,
        conversation_namespace="chat",
        conversation_source_id="conversation-1",
    )

    assert event_fingerprint(
        event,
        conversation_namespace="chat",
        conversation_source_id="conversation-1",
    ) == fingerprint_value(payload)


def test_record_and_asset_fingerprints_exclude_authority_and_source_location() -> None:
    record = NormalizedRecord(
        source="google",
        record_type="contact",
        source_record_namespace="contacts",
        source_record_id="1",
        title="Ada",
    )
    assert record_fingerprint(record) == record_fingerprint(
        record.model_copy(
            update={
                "authority_at": datetime(2026, 9, 10, tzinfo=UTC),
                "authority_kind": "exported_at",
            }
        )
    )
    assert record_fingerprint(record) != record_fingerprint(
        record.model_copy(update={"title": "Grace"})
    )

    asset = NormalizedAsset(
        source="google",
        asset_namespace="photos",
        source_asset_id="1",
        filename="photo.jpg",
        original_path="C:/first.zip!/photo.jpg",
        size_bytes=1,
    )
    moved = asset.model_copy(update={"original_path": "D:/second.zip!/photo.jpg"})
    assert asset_fingerprint(asset) == asset_fingerprint(moved)
