from personal_data_lab.models import ImportResult


def test_reconciliation_requires_both_counts() -> None:
    assert ImportResult(
        source_file_id=1,
        source_conversations=2,
        database_conversations=2,
        source_events=3,
        database_events=3,
    ).reconciled
    assert not ImportResult(
        source_file_id=1,
        source_conversations=2,
        database_conversations=2,
        source_events=3,
        database_events=2,
    ).reconciled
