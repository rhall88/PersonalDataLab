"""Phase 7's semantic annotation (PHASE7_SEMANTIC_ANNOTATION_IMPLEMENTATION_PLAN.md): topic
labels over Phase 6's semantic.text_unit population via a grammar-constrained local GGUF model.

`signal_annotation_topic` (LOCAL, `inputs=("semantic_text_unit",)`, 1:1 partition mapping) is the
codebase's first SEMANTIC-class deriver and its first real use of `deriver_version.model_ref`:
model identity, the fixed prompt, and the topic taxonomy are all hashed into it, so any of the
three changing produces a new deriver version and re-annotates under the framework's existing
dirty tracking (decision 4) with zero framework changes. The output label is constrained by a
GBNF grammar built from `_TOPIC_LABELS` at the token-sampling level, not by prompt instruction
alone (decision 2) -- an out-of-taxonomy label is structurally impossible, not just unlikely.

Per decision 6, the real production model (Phi-3.5-mini-instruct) is not wired in yet -- disk
space is tight and an external-drive migration is in progress in parallel. `PDL_ANNOTATION_MODEL_PATH`
must point at a real .gguf file for this deriver to actually annotate anything; if unset (or the
path doesn't exist), registration still succeeds with placeholder hashes in `model_ref` -- only
`compute()` itself refuses to run, the same "registered but not runnable yet" state this phase's
plan explicitly intends.
"""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ...fingerprints import fingerprint_value
from ..partitions import parse_local_key
from ..registry import ComputeResult, deriver

_MODEL_PATH_ENV = "PDL_ANNOTATION_MODEL_PATH"
_MAX_TOKENS = 16
_N_CTX = 4096

# Decision 2's starter taxonomy -- a versioned constant, not a value invented per call. Changing
# this tuple changes topic_labels_hash below, which changes model_ref, which is a new deriver
# version per the framework's existing "model_ref/params/code_version all feed version_hash"
# behavior (versioning.py) -- no special-case machinery needed for a taxonomy change.
_TOPIC_LABELS: tuple[str, ...] = (
    "family", "health", "finances", "work", "relationships", "logistics",
    "technology", "travel", "creative_projects", "news_politics", "food",
    "housing", "legal", "education", "entertainment", "shopping", "pets", "other",
)

_PROMPT_TEMPLATE = (
    "Read the text below and respond with exactly one topic label from this list: "
    + ", ".join(_TOPIC_LABELS)
    + '.\n\nText:\n"""\n{text}\n"""\n\nTopic:'
)

# A GBNF grammar whose root is a plain alternation of the taxonomy's literal strings. llama.cpp
# enforces this at the token-sampling level, so the model is structurally unable to emit anything
# outside _TOPIC_LABELS regardless of what its own (possibly untrained/random, see decision 6's
# test model) weights would otherwise produce.
_GRAMMAR_SOURCE = "root ::= " + " | ".join(f'"{label}"' for label in _TOPIC_LABELS) + "\n"


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compute_model_ref() -> dict:
    """Model identity for deriver_version.model_ref (decision 4). Read once at module import
    time (decorator application), so tests that want to see it change set PDL_ANNOTATION_MODEL_PATH
    and call this function directly rather than relying on process-lifetime re-registration.
    """
    path = os.environ.get(_MODEL_PATH_ENV)
    weights_hash = _file_sha256(path) if path and Path(path).is_file() else "unconfigured"
    return {
        "model_id": path or None,
        "weights_sha256": weights_hash,
        "prompt_hash": hashlib.sha256(_PROMPT_TEMPLATE.encode()).hexdigest(),
        "topic_labels_hash": hashlib.sha256("|".join(_TOPIC_LABELS).encode()).hexdigest(),
    }


_model = None
_grammar = None


def _get_model():
    global _model, _grammar
    if _model is None:
        path = os.environ.get(_MODEL_PATH_ENV)
        if not path or not Path(path).is_file():
            raise RuntimeError(
                f"{_MODEL_PATH_ENV} is not set to a real .gguf file -- no annotation model is "
                "configured yet (PHASE7_SEMANTIC_ANNOTATION_IMPLEMENTATION_PLAN.md decision 6)"
            )
        from llama_cpp import Llama, LlamaGrammar

        _model = Llama(model_path=path, n_ctx=_N_CTX, logits_all=True, verbose=False)
        _grammar = LlamaGrammar.from_string(_GRAMMAR_SOURCE)
    return _model, _grammar


def _annotate_one(text_unit_id: int, unit_text: str, epoch_id: int) -> dict:
    model, grammar = _get_model()
    prompt = _PROMPT_TEMPLATE.format(text=unit_text)
    output = model(
        prompt, grammar=grammar, max_tokens=_MAX_TOKENS, temperature=0.0, logprobs=1,
    )
    choice = output["choices"][0]
    value = choice["text"].strip()
    token_logprobs = [
        lp for lp in (choice.get("logprobs") or {}).get("token_logprobs", []) if lp is not None
    ]
    score = math.exp(sum(token_logprobs) / len(token_logprobs)) if token_logprobs else 0.0
    return {
        "text_unit_id": text_unit_id,
        "kind": "topic",
        "value": value,
        "score": min(max(score, 0.0), 1.0),
        "computed_epoch_id": epoch_id,
    }


def _clear_annotation(connection: Connection, partition_key: str) -> None:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        return
    connection.execute(
        text(
            """
            DELETE FROM signal.annotation
            WHERE kind = 'topic' AND text_unit_id IN (
                SELECT id FROM semantic.text_unit
                WHERE source = :source AND started_at >= :start AND started_at < :end
            )
            """
        ),
        {"source": source, "start": start, "end": end},
    )


@deriver(
    name="signal_annotation_topic",
    partition_class="LOCAL",
    final_inference="SEMANTIC",
    description=(
        "Grammar-constrained topic labels over semantic.text_unit "
        "(PHASE7_SEMANTIC_ANNOTATION_IMPLEMENTATION_PLAN.md decisions 2-4)."
    ),
    output_relation="signal.annotation",
    inputs=("semantic_text_unit",),
    model_ref=_compute_model_ref(),
    clear_partition=_clear_annotation,
)
def signal_annotation_topic(
    connection: Connection, partition_key: str, epoch_id: int
) -> ComputeResult:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        return ComputeResult(input_fingerprint=fingerprint_value(["undated"]), rows=[])

    units = connection.execute(
        text(
            "SELECT id, text FROM semantic.text_unit "
            "WHERE source = :source AND started_at >= :start AND started_at < :end ORDER BY id"
        ),
        {"source": source, "start": start, "end": end},
    ).all()
    fingerprint = fingerprint_value([[u.id, u.text] for u in units])
    if not units:
        return ComputeResult(input_fingerprint=fingerprint, rows=[])

    rows = [_annotate_one(u.id, u.text, epoch_id) for u in units]
    return ComputeResult(input_fingerprint=fingerprint, rows=rows)
