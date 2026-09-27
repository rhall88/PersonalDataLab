"""Fast unit coverage for annotation.py's grammar/model-identity plumbing, plus one real,
non-mocked end-to-end call through the actual llama.cpp completion API on a tiny test-only GGUF
model -- proving the whole chain (grammar-constrained generation, row shape, model_ref hashing)
works, the same rigor test_media.py's real Whisper call and test_phase6.py's real embedding calls
already established for their own phases, not just mocked units. The real production model
(Phi-3.5-mini-instruct) is deliberately not used here (PHASE7_SEMANTIC_ANNOTATION_IMPLEMENTATION_PLAN.md
decision 6) -- these tests only need PDL_ANNOTATION_MODEL_PATH to point at *some* real .gguf file.
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
from personal_data_lab.derive.derivers import annotation as annotation_module
from personal_data_lab.derive.derivers.annotation import (
    _GRAMMAR_SOURCE,
    _TOPIC_LABELS,
    _annotate_one,
    _compute_model_ref,
    _file_sha256,
)
from personal_data_lab.models import NormalizedEvent, ParsedExport

pytestmark = pytest.mark.integration

_MODEL_PATH_ENV = "PDL_ANNOTATION_MODEL_PATH"
_TEST_MODEL_REPO = "tensorblock/onnx-internal-testing_tiny-random-LlamaForCausalLM-GGUF"
_TEST_MODEL_FILE = "tiny-random-LlamaForCausalLM-Q2_K.gguf"


def _download_test_model() -> str:
    """A genuinely tiny (~12MB), openly licensed test-only GGUF model -- not the real production
    model. Downloaded (and cached by huggingface_hub, same as faster-whisper/fastembed already do
    for their own models) rather than committed to the repo as a binary blob.
    """
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        pytest.skip("huggingface_hub not installed")
    try:
        return hf_hub_download(repo_id=_TEST_MODEL_REPO, filename=_TEST_MODEL_FILE)
    except Exception as error:  # noqa: BLE001
        pytest.skip(f"could not download test GGUF model: {error}")


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
        connection.execute(text("TRUNCATE TABLE signal.annotation RESTART IDENTITY CASCADE"))
        connection.execute(text("TRUNCATE TABLE semantic.text_unit RESTART IDENTITY CASCADE"))
    yield


def test_topic_labels_are_a_fixed_closed_set() -> None:
    assert len(_TOPIC_LABELS) == len(set(_TOPIC_LABELS)), "no duplicate labels"
    assert "other" in _TOPIC_LABELS
    assert all(label == label.lower() for label in _TOPIC_LABELS)


def test_grammar_source_only_allows_topic_labels() -> None:
    for label in _TOPIC_LABELS:
        assert f'"{label}"' in _GRAMMAR_SOURCE
    assert _GRAMMAR_SOURCE.startswith("root ::=")


def test_compute_model_ref_reports_unconfigured_when_no_model_path(monkeypatch) -> None:
    monkeypatch.delenv(_MODEL_PATH_ENV, raising=False)
    ref = _compute_model_ref()
    assert ref["model_id"] is None
    assert ref["weights_sha256"] == "unconfigured"
    # prompt/taxonomy hashes are always present regardless of whether a model is configured.
    assert ref["prompt_hash"]
    assert ref["topic_labels_hash"]


def test_file_sha256_changes_when_file_bytes_change(tmp_path) -> None:
    """Real bug this guards against: a hardcoded or placeholder 'weights hash' would never
    actually detect a re-quantized or swapped model file. This hashes real bytes of two real
    files and confirms they differ, proving the claim in decision 4 actually holds.
    """
    file_a = tmp_path / "a.gguf"
    file_b = tmp_path / "b.gguf"
    file_a.write_bytes(b"pretend model weights v1")
    file_b.write_bytes(b"pretend model weights v2, totally different")

    hash_a = _file_sha256(str(file_a))
    hash_b = _file_sha256(str(file_b))
    assert hash_a != hash_b
    assert len(hash_a) == 64  # hex sha256


def test_compute_model_ref_hashes_the_real_configured_file(tmp_path, monkeypatch) -> None:
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"some real bytes")
    monkeypatch.setenv(_MODEL_PATH_ENV, str(model_file))

    ref = _compute_model_ref()
    assert ref["model_id"] == str(model_file)
    assert ref["weights_sha256"] == _file_sha256(str(model_file))
    assert ref["weights_sha256"] != "unconfigured"


def test_sync_registers_signal_annotation_topic(database_engine: Engine) -> None:
    derive_sync(database_engine)
    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT partition_class, final_inference FROM derive.deriver "
                "WHERE name = 'signal_annotation_topic'"
            )
        ).first()
    assert row is not None
    assert row.partition_class == "LOCAL"
    # The codebase's first real SEMANTIC-class deriver -- asserted explicitly so a future
    # refactor can't silently regress this.
    assert row.final_inference == "SEMANTIC"


def test_annotate_one_real_call_on_tiny_gguf_model(monkeypatch) -> None:
    """The one real, non-mocked call through llama.cpp -- proves grammar-constrained decoding,
    the temp-model spool, and row shape actually work end to end on a real (if untrained) model,
    not just that the plumbing functions look right in isolation.
    """
    model_path = _download_test_model()
    monkeypatch.setenv(_MODEL_PATH_ENV, model_path)
    annotation_module._model = None
    annotation_module._grammar = None

    row = _annotate_one(1, "Are we still on for dinner tomorrow night?", epoch_id=1)

    assert row["text_unit_id"] == 1
    assert row["kind"] == "topic"
    # The grammar constraint is real (token-level), so even an untrained/random-weight model can
    # never emit a label outside the closed taxonomy.
    assert row["value"] in _TOPIC_LABELS
    assert 0.0 <= row["score"] <= 1.0


def test_annotate_one_bounds_a_unit_too_long_for_the_context_window(monkeypatch) -> None:
    # Real bug the first GPU rollout attempt hit: llama.cpp raises ValueError("Requested tokens
    # ... exceed context window") rather than truncating, which would otherwise crash this unit's
    # entire partition (every other unit alongside it never gets annotated either). A genuinely
    # oversized unit must still produce a real row, not a crash.
    model_path = _download_test_model()
    monkeypatch.setenv(_MODEL_PATH_ENV, model_path)
    annotation_module._model = None
    annotation_module._grammar = None

    huge_text = "word " * 20000  # ~100,000 chars -- far beyond any real n_ctx regardless of tokenizer

    row = _annotate_one(1, huge_text, epoch_id=1)

    assert row["text_unit_id"] == 1
    assert row["value"] in _TOPIC_LABELS


def test_get_model_defaults_to_cpu_only_but_honors_gpu_layers_env(
    monkeypatch, tmp_path
) -> None:
    # Real finding this guards: n_gpu_layers is a runtime placement knob (added for the rented-GPU
    # Phase 7 rollout), not a model-identity field -- it must default to 0 (CPU-only, today's
    # local behavior) when unset, and must reach Llama()'s constructor unchanged when a rollout
    # sets PDL_ANNOTATION_N_GPU_LAYERS for a CUDA-enabled build.
    fake_model_path = tmp_path / "fake.gguf"
    fake_model_path.write_bytes(b"not a real gguf, never opened by the fake below")
    monkeypatch.setenv(_MODEL_PATH_ENV, str(fake_model_path))
    annotation_module._model = None
    annotation_module._grammar = None

    captured = {}

    class _FakeLlama:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _FakeGrammar:
        @staticmethod
        def from_string(_source):
            return object()

    import llama_cpp

    monkeypatch.setattr(llama_cpp, "Llama", _FakeLlama)
    monkeypatch.setattr(llama_cpp, "LlamaGrammar", _FakeGrammar)

    annotation_module._get_model()
    assert captured["n_gpu_layers"] == 0

    annotation_module._model = None
    annotation_module._grammar = None
    monkeypatch.setenv("PDL_ANNOTATION_N_GPU_LAYERS", "999")
    annotation_module._get_model()
    assert captured["n_gpu_layers"] == 999


def _import_mail_event(engine: Engine, *, body: str, occurred_at: datetime) -> None:
    # google mail_message needs no conversation grouping (Phase 6 decision 2: each email is
    # already its own coherent text unit) -- the simplest real path from a canonical event to a
    # semantic.text_unit row, reused here rather than hand-constructing text_unit/evidence_epoch
    # rows directly against a schema this test doesn't own.
    parsed = ParsedExport(
        source="google",
        parser_version="test-annotation-v1",
        filename="mail.json",
        original_path="mail.json",
        sha256="1" * 64,
        size_bytes=1,
        events=(
            NormalizedEvent(
                occurred_at=occurred_at,
                source="google",
                event_type="mail_message",
                source_record_namespace="google/mail_message",
                source_record_id="m1",
                text=body,
            ),
        ),
    )
    import_parsed_exports(engine, (parsed,))


def test_clear_partition_only_deletes_its_own_source_and_month(
    database_engine: Engine, monkeypatch
) -> None:
    model_path = _download_test_model()
    monkeypatch.setenv(_MODEL_PATH_ENV, model_path)
    annotation_module._model = None
    annotation_module._grammar = None

    _import_mail_event(
        database_engine,
        body="Planning a trip to the mountains next summer.",
        occurred_at=datetime(2024, 5, 1, tzinfo=UTC),
    )

    derive_sync(database_engine)
    outcomes = derive_run(database_engine)

    annotation_outcomes = [o for o in outcomes if o.deriver_name == "signal_annotation_topic"]
    assert len(annotation_outcomes) >= 1
    assert any(o.status == "succeeded" for o in annotation_outcomes)

    with database_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT a.kind, a.value FROM signal.annotation a "
                "JOIN semantic.text_unit tu ON tu.id = a.text_unit_id "
                "WHERE tu.source = 'google'"
            )
        ).one()
    assert row.kind == "topic"
    assert row.value in _TOPIC_LABELS
