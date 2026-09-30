"""Exit tests 1-6 from PHASE8_DOCUMENT_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md (exit test 7, the
real-database rollout, is done live against the real canonical database, not here). Mirrors
tests/test_phase6.py's fixtures, extended to also reset Phase 8's semantic.*_document tables.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text

from personal_data_lab.database import create_database_engine, import_parsed_exports
from personal_data_lab.derive import run as derive_run
from personal_data_lab.derive import sync as derive_sync
from personal_data_lab.derive.derivers.semantic import search as semantic_search
from personal_data_lab.models import NormalizedRecord, ParsedExport

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
            text(
                "TRUNCATE TABLE semantic.text_unit_document, "
                "semantic.embedding_bge_small_en_v15_document RESTART IDENTITY CASCADE"
            )
        )
    yield


def _import_record(
    engine: Engine, *, source: str, namespace: str, source_record_id: str,
    record_type: str = "document", text_body: str, occurred_at: datetime | None,
) -> None:
    parsed = ParsedExport(
        source=source,
        parser_version="test-phase8-v1",
        filename="doc.html",
        original_path="doc.html",
        sha256="c" * 64,
        size_bytes=1,
        records=(
            NormalizedRecord(
                source=source,
                record_type=record_type,
                source_record_namespace=namespace,
                source_record_id=source_record_id,
                text=text_body,
                occurred_at=occurred_at,
            ),
        ),
    )
    import_parsed_exports(engine, (parsed,))


def test_sync_registers_document_derivers(database_engine: Engine) -> None:
    derive_sync(database_engine)
    with database_engine.connect() as connection:
        text_unit_row = connection.execute(
            text(
                "SELECT partition_class FROM derive.deriver WHERE name = 'semantic_text_unit_document'"
            )
        ).first()
        embed_row = connection.execute(
            text(
                "SELECT partition_class FROM derive.deriver "
                "WHERE name = 'semantic_embedding_document_bge_small'"
            )
        ).first()
    assert text_unit_row is not None
    assert text_unit_row.partition_class == "HALO"
    assert embed_row is not None
    assert embed_row.partition_class == "LOCAL"


def test_allowlisted_namespace_produces_a_unit_disallowed_does_not(database_engine: Engine) -> None:
    _import_record(
        database_engine, source="google",
        namespace="google_takeout/notebooklm/document/html",
        source_record_id="r1", text_body="Real NotebookLM source content worth embedding.",
        occurred_at=datetime(2024, 5, 1, tzinfo=UTC),
    )
    _import_record(
        database_engine, source="google",
        namespace="google_takeout/drive/document/csv",
        source_record_id="r2", text_body="2024-05-01,event,noise,not,prose",
        occurred_at=datetime(2024, 5, 1, tzinfo=UTC),
    )

    derive_sync(database_engine)
    derive_run(database_engine)

    with database_engine.connect() as connection:
        rows = connection.execute(
            text("SELECT source, text, truncated FROM semantic.text_unit_document")
        ).all()
    assert len(rows) == 1
    assert rows[0].source == "google"
    assert "NotebookLM" in rows[0].text
    assert rows[0].truncated is False


def test_long_document_is_chunked_not_truncated(database_engine: Engine) -> None:
    """v2 (migration 029): a long document becomes ordered chunks covering all of it, instead of
    one unit cut off at 8,000 characters."""
    long_text = "x" * 9_000
    _import_record(
        database_engine, source="google",
        namespace="google_takeout/drive/document/txt",
        source_record_id="r1", text_body=long_text,
        occurred_at=datetime(2024, 5, 1, tzinfo=UTC),
    )

    derive_sync(database_engine)
    derive_run(database_engine)

    with database_engine.connect() as connection:
        rows = connection.execute(
            text("SELECT chunk_index, length(text) AS n, truncated FROM semantic.text_unit_document "
                 "ORDER BY chunk_index")
        ).all()
    assert [r.chunk_index for r in rows] == [0, 1]
    assert sum(r.n for r in rows) == 9_000
    assert not any(r.truncated for r in rows)


def test_undated_record_still_lands_in_a_partition_and_is_processed(database_engine: Engine) -> None:
    """The scheduler fix media_transcribe_whisper's rollout found (undated bucket never seeded)
    must hold for this second, independent root deriver too, not just the one it was found in.
    """
    _import_record(
        database_engine, source="google",
        namespace="google_takeout/recorder/document/txt",
        source_record_id="r1", text_body="Speaker 1: a real transcript with no known date.",
        occurred_at=None,
    )

    derive_sync(database_engine)
    outcomes = derive_run(database_engine)

    unit_outcomes = [o for o in outcomes if o.deriver_name == "semantic_text_unit_document"]
    assert any(o.status == "succeeded" for o in unit_outcomes)
    with database_engine.connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM semantic.text_unit_document")
        ).scalar_one()
    assert count == 1


def test_embeddings_produced_and_rerun_is_a_noop(database_engine: Engine) -> None:
    _import_record(
        database_engine, source="google",
        namespace="apple_privacy/notes/note/record",
        source_record_id="r1", text_body="A real note worth embedding.",
        occurred_at=datetime(2024, 5, 1, tzinfo=UTC),
    )

    derive_sync(database_engine)
    derive_run(database_engine)

    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT vector_dims(vector) AS dims FROM "
                "semantic.embedding_bge_small_en_v15_document"
            )
        ).one()
    assert row.dims == 384

    second_outcomes = derive_run(database_engine)
    embedding_outcomes = [
        o for o in second_outcomes if o.deriver_name == "semantic_embedding_document_bge_small"
    ]
    assert embedding_outcomes == [] or all(
        o.status == "skipped_unchanged" for o in embedding_outcomes
    )


def test_semantic_search_include_documents_merges_by_distance(database_engine: Engine) -> None:
    _import_record(
        database_engine, source="google",
        namespace="google_takeout/notebooklm/document/html",
        source_record_id="r1",
        text_body="The grandmother moved into assisted living and the family helped pack the house.",
        occurred_at=datetime(2024, 5, 1, tzinfo=UTC),
    )

    derive_sync(database_engine)
    derive_run(database_engine)

    without_documents = semantic_search(database_engine, "grandmother moving", include_documents=False)
    assert without_documents == []

    with_documents = semantic_search(database_engine, "grandmother moving", include_documents=True)
    assert len(with_documents) == 1
    assert with_documents[0]["kind"] == "document"
    assert "grandmother" in with_documents[0]["text"]
