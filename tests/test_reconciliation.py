from datetime import UTC, datetime

from personal_data_lab.reconciliation import (
    Authority,
    authority_advances,
    classify_changed_authority,
)


def test_authority_classification_never_uses_ingestion_order() -> None:
    older = Authority(datetime(2025, 1, 1, tzinfo=UTC), "provider_updated_at")
    newer = Authority(datetime(2026, 1, 1, tzinfo=UTC), "provider_updated_at")

    assert classify_changed_authority(older, newer) == "updated"
    assert classify_changed_authority(newer, older) == "stale"
    assert classify_changed_authority(newer, newer) == "conflicted"


def test_incomparable_or_unknown_changed_authority_conflicts() -> None:
    provider = Authority(datetime(2025, 1, 1, tzinfo=UTC), "provider_updated_at")
    snapshot = Authority(datetime(2026, 1, 1, tzinfo=UTC), "exported_at")
    unknown = Authority(None, "unknown")

    assert classify_changed_authority(provider, snapshot) == "conflicted"
    assert classify_changed_authority(provider, unknown) == "conflicted"
    assert classify_changed_authority(unknown, provider) == "updated"


def test_same_content_watermark_advances_only_with_comparable_evidence() -> None:
    older = Authority(datetime(2025, 1, 1, tzinfo=UTC), "exported_at")
    newer = Authority(datetime(2026, 1, 1, tzinfo=UTC), "exported_at")
    different_kind = Authority(datetime(2027, 1, 1, tzinfo=UTC), "provider_updated_at")

    assert authority_advances(older, newer)
    assert not authority_advances(newer, older)
    assert not authority_advances(older, different_kind)
