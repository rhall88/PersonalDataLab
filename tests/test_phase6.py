"""Exit tests 1-7 from PHASE6_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md (exit test 8, the real-database
rollout, is done live against the real canonical database, not here). Mirrors tests/test_phase5.py's
fixtures, extended to also reset Phase 6's semantic.* tables between tests.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text

from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.derive import run as derive_run
from personal_data_lab.derive import sync as derive_sync
from personal_data_lab.derive.derivers.semantic import search as semantic_search
from personal_data_lab.models import NormalizedConversation, NormalizedEvent, ParsedExport

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    test_database_url = os.getenv("TEST_DATABASE_URL")
    if not test_database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_database_engine(test_database_url)
    with engine.connect() as connection:
        database_name = str(connection.execute(text("SELECT current_database()")).scalar_one())
    if not database_name.endswith("_test"):
        engine.dispose()
        pytest.fail("integration tests require a database name ending in '_test'")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(database_engine: Engine) -> Iterator[None]:
    with database_engine.begin() as connection:
        connection.execute(
            text(
                """
                TRUNCATE TABLE
                    relationship_observations, asset_observations, record_observations,
                    event_observations, conversation_observations, attachment_observations,
                    event_versions, conversation_versions, attachment_versions,
                    asset_versions, record_versions, relationship_versions,
                    import_run_source_files, source_file_locations, attachments,
                    source_file_assets, source_file_relationships, source_file_records,
                    source_file_events, source_file_conversations, events, assets, records,
                    relationships, identities, people, conversations, source_files, import_runs
                RESTART IDENTITY CASCADE
                """
            )
        )
        connection.execute(
            text(
                "TRUNCATE TABLE derive.derivation_run, derive.partition_state, "
                "derive.evidence_epoch RESTART IDENTITY CASCADE"
            )
        )
        connection.execute(text("UPDATE derive.change_cursor SET last_import_run_id = 0"))
        connection.execute(
            text("TRUNCATE TABLE semantic.text_unit, semantic.embedding_bge_small_en_v15 RESTART IDENTITY CASCADE")
        )
    yield


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _import_events(engine: Engine, source: str, filename: str, specs: list[dict]) -> None:
    by_conversation: dict[str, list[NormalizedEvent]] = {}
    standalone: list[NormalizedEvent] = []
    for spec in specs:
        normalized = NormalizedEvent(
            occurred_at=spec["occurred_at"],
            source=source,
            event_type=spec["event_type"],
            source_record_namespace=f"{source}/event",
            source_record_id=spec["source_record_id"],
            text=spec["text"],
        )
        conversation_id = spec.get("conversation_id")
        if conversation_id:
            by_conversation.setdefault(conversation_id, []).append(normalized)
        else:
            standalone.append(normalized)

    conversations = tuple(
        NormalizedConversation(
            source=source,
            source_conversation_id=conversation_id,
            source_record_namespace=f"{source}/conversation",
            events=tuple(events),
        )
        for conversation_id, events in by_conversation.items()
    )
    parsed = ParsedExport(
        source=source,
        parser_version="test-phase6-v1",
        filename=filename,
        original_path=filename,
        sha256=_sha(filename),
        size_bytes=1,
        events=tuple(standalone),
        conversations=conversations,
    )
    import_parsed_exports(engine, (parsed,))


def _run_semantic(engine: Engine) -> None:
    derive_sync(engine)
    derive_run(engine)


def test_sync_registers_semantic_derivers(database_engine: Engine) -> None:
    derive_sync(database_engine)
    with database_engine.connect() as connection:
        rows = dict(
            connection.execute(
                text(
                    "SELECT name, partition_class FROM derive.deriver "
                    "WHERE name IN ('semantic_text_unit', 'semantic_embedding_bge_small')"
                )
            ).all()
        )
    assert rows == {"semantic_text_unit": "HALO", "semantic_embedding_bge_small": "LOCAL"}


def test_facebook_session_grouping_splits_on_45_minute_gap(database_engine: Engine) -> None:
    base = datetime(2024, 3, 15, 10, 0, tzinfo=UTC)
    _import_events(
        database_engine, "facebook", "fb.json",
        [
            {"occurred_at": base, "event_type": "message", "source_record_id": "1", "text": "hi", "conversation_id": "c1"},
            {"occurred_at": base + timedelta(minutes=10), "event_type": "message", "source_record_id": "2", "text": "hey", "conversation_id": "c1"},
            {"occurred_at": base + timedelta(minutes=60), "event_type": "message", "source_record_id": "3", "text": "later msg", "conversation_id": "c1"},
        ],
    )
    _run_semantic(database_engine)

    with database_engine.connect() as connection:
        units = connection.execute(
            text("SELECT event_ids, text FROM semantic.text_unit WHERE source='facebook' ORDER BY started_at")
        ).all()
    assert len(units) == 2
    assert len(units[0].event_ids) == 2
    assert units[0].text == "hi\n\nhey"
    assert len(units[1].event_ids) == 1
    assert units[1].text == "later msg"


def test_openai_conversation_is_one_unit_regardless_of_gaps(database_engine: Engine) -> None:
    base = datetime(2024, 3, 15, 10, 0, tzinfo=UTC)
    _import_events(
        database_engine, "openai", "oa.json",
        [
            {"occurred_at": base, "event_type": "conversation_message", "source_record_id": "1", "text": "one", "conversation_id": "c1"},
            {"occurred_at": base + timedelta(hours=2), "event_type": "conversation_message", "source_record_id": "2", "text": "two", "conversation_id": "c1"},
            {"occurred_at": base + timedelta(hours=5), "event_type": "conversation_message", "source_record_id": "3", "text": "three", "conversation_id": "c1"},
        ],
    )
    _run_semantic(database_engine)

    with database_engine.connect() as connection:
        units = connection.execute(
            text("SELECT event_ids, text FROM semantic.text_unit WHERE source='openai'")
        ).all()
    assert len(units) == 1
    assert len(units[0].event_ids) == 3
    assert units[0].text == "one\n\ntwo\n\nthree"


def test_google_mail_is_never_grouped(database_engine: Engine) -> None:
    base = datetime(2024, 3, 15, 10, 0, tzinfo=UTC)
    _import_events(
        database_engine, "google", "mail.json",
        [
            {"occurred_at": base, "event_type": "mail_message", "source_record_id": "1", "text": "first email"},
            {"occurred_at": base + timedelta(minutes=1), "event_type": "mail_message", "source_record_id": "2", "text": "second email"},
        ],
    )
    _run_semantic(database_engine)

    with database_engine.connect() as connection:
        units = connection.execute(
            text("SELECT event_ids FROM semantic.text_unit WHERE source='google' ORDER BY started_at")
        ).all()
    assert len(units) == 2
    assert all(len(u.event_ids) == 1 for u in units)


def test_long_unit_is_truncated_not_dropped(database_engine: Engine) -> None:
    base = datetime(2024, 3, 15, 10, 0, tzinfo=UTC)
    long_text = "x" * 9_000
    _import_events(
        database_engine, "google", "mail_long.json",
        [{"occurred_at": base, "event_type": "mail_message", "source_record_id": "1", "text": long_text}],
    )
    _run_semantic(database_engine)

    with database_engine.connect() as connection:
        unit = connection.execute(
            text("SELECT text, truncated FROM semantic.text_unit WHERE source='google'")
        ).one()
    assert unit.truncated is True
    assert len(unit.text) == 8_000


def test_embeddings_produced_and_rerun_is_a_noop(database_engine: Engine) -> None:
    base = datetime(2024, 3, 15, 10, 0, tzinfo=UTC)
    _import_events(
        database_engine, "google", "mail_embed.json",
        [{"occurred_at": base, "event_type": "mail_message", "source_record_id": "1", "text": "a short real email"}],
    )
    derive_sync(database_engine)
    outcomes = derive_run(database_engine)

    # semantic_text_unit's 1-day-each-way halo dirties the neighboring months too (see
    # test_derive.py's dirty-set test), so semantic_embedding_bge_small runs for all three --
    # the other two just have no text units and write zero rows.
    embed_outcomes = {o.partition_key: o for o in outcomes if o.deriver_name == "semantic_embedding_bge_small"}
    assert set(embed_outcomes) == {"google:2024-02", "google:2024-03", "google:2024-04"}
    assert {o.status for o in embed_outcomes.values()} == {"succeeded"}
    assert embed_outcomes["google:2024-03"].rows_written == 1

    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT vector_dims(vector) AS dims FROM semantic.embedding_bge_small_en_v15 e "
                "JOIN semantic.text_unit tu ON tu.id = e.text_unit_id WHERE tu.source='google'"
            )
        ).one()
    assert row.dims == 384

    # Nothing new since the last sync -- re-running should skip, not re-embed.
    second_sync = derive_sync(database_engine)
    assert second_sync.is_noop


def test_semantic_search_ranks_the_matching_unit_first(database_engine: Engine) -> None:
    base = datetime(2024, 3, 15, 10, 0, tzinfo=UTC)
    _import_events(
        database_engine, "google", "mail_search.json",
        [
            {"occurred_at": base, "event_type": "mail_message", "source_record_id": "1", "text": "Your quarterly tax filing deadline is approaching"},
            {"occurred_at": base + timedelta(minutes=1), "event_type": "mail_message", "source_record_id": "2", "text": "Grandma is moving into a new assisted living apartment this week"},
            {"occurred_at": base + timedelta(minutes=2), "event_type": "mail_message", "source_record_id": "3", "text": "Your package has shipped and is on its way"},
        ],
    )
    _run_semantic(database_engine)

    hits = semantic_search(database_engine, "grandmother moving into assisted living", limit=3)
    assert hits[0]["text"] == "Grandma is moving into a new assisted living apartment this week"
