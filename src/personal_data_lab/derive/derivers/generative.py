"""Phase 10's single consolidated generative pass (PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md
decision 1): `generative_extract_text_unit` reads semantic.text_unit's raw text exactly once and
emits one structured JSON envelope per unit into generative.extraction. Everything else in the
life graph (entities, relations, events, decisions, open loops, contradictions) is pure logic over
copies/derivations of this table's output in other modules -- nothing else ever calls the model on
raw text again (decision 1), and generative.extraction itself is never altered after it's written
(decision 11): it is the immutable record of what the model actually said.

Same model as Phase 7 (decision 2), same rented-GPU pattern, same env vars
(PDL_ANNOTATION_MODEL_PATH/PDL_ANNOTATION_N_GPU_LAYERS) -- this is annotation.py's second real use,
not a new one. `_bounded_prompt` is duplicated rather than imported: annotation.py's version is
tied to its own (much shorter) prompt template, and the two prompts' token overhead genuinely
differs, so a shared helper would need a prompt-agnostic signature that isn't worth the indirection
for one duplicated ~10-line function.

Envelope schema, grammar, and token budget were grounded against a real, timed benchmark run on
2026-09-27 -- a stratified 273-unit sample (all 5 sources, three length buckets) run through
llama.cpp's actual continuous-batching server, not a heuristic:
- real p50/p90 entity counts were 3/5-6 per unit, relations 2/5-6 per unit (decision 9), real max
  observed was 10/8 -- the grammar's entities/relations arrays are bounded to a generous 20/15
  ceiling (well above that observed max), not left fully unbounded as the benchmark's own
  throwaway grammar was. This project's later Phase 10 exit tests (run against a real, if
  untrained, GGUF model to prove the grammar's structural guarantees rather than just asserting
  them) found that fully unbounded repetition -- in `ws`, in a string's own character content, and
  in these two arrays -- is a real pathological-generation path: a model can get stuck repeating an
  unbounded branch indefinitely and never terminate the JSON object within any token budget. A
  generous bound removes that failure mode for production (a trained model was never going to need
  it) while still comfortably exceeding every real count this benchmark measured.
- real tokens_predicted distribution was p50=280, p90=503, p99=768, with the p99/max both pinned to
  that run's 768-token cap -- meaning even 768 still truncated a real tail. _MAX_TOKENS is set well
  above that observed ceiling (not the cap that was already seen truncating) to keep partitions
  from losing real data to truncation, the same failure mode annotation.py's own _bounded_prompt
  fix exists to prevent on the input side.
- the benchmark also found and fixed a real grammar-parser quirk: this llama.cpp build's GBNF
  parser silently fails to parse a rule production written across multiple physical lines (contrary
  to the multi-line style used in llama.cpp's own upstream example grammars) -- every rule below is
  written on one line for that reason, not for compactness.
- the benchmark's own simplified string rule (no escape support, unbounded content) missed both of
  these production bugs -- it never combined real escape sequences with a bare control character,
  and its short throwaway prompts never ran long enough to hit an unbounded-repetition stall. Both
  were only found once this phase's own exit tests ran the *real* grammar end-to-end.
- the benchmark's simplified string rule (disallowing only a literal `"`, with no backslash-escape
  support) produced one real invalid-JSON row out of 273: a technical conversation whose text
  contained a literal Windows device path (backslashes). PRODUCTION_STRING below restores real
  JSON string escaping (quote/backslash/control-character-safe) -- confirmed parseable by this same
  llama.cpp build once written as a single line, unlike the benchmark's throwaway version.

Decision 8's slot-validity/evidence-mode hardening is deliberately NOT applied here: those rules
constrain what becomes an `entity.relation_candidate` row, not what the model is allowed to have
said. They live in entity.py's relation_candidate_extract, which reads this table's raw
relation_candidates JSONB and is the deriver actually responsible for filtering before a row is
written -- consistent with decision 11's "generative.extraction is never altered after it's
written."
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ...fingerprints import fingerprint_value
from ..partitions import parse_local_key
from ..registry import ComputeResult, deriver

_MODEL_PATH_ENV = "PDL_ANNOTATION_MODEL_PATH"
_GPU_LAYERS_ENV = "PDL_ANNOTATION_N_GPU_LAYERS"
_MAX_TOKENS = 1536
_N_CTX = 8192
_SAFETY_MARGIN = 8

_INTERACTION_LABELS: tuple[str, ...] = (
    "support", "conflict", "planning", "affection", "logistics", "financial",
    "creative_collaboration", "advice", "argument", "reconciliation",
)
_ENTITY_KINDS: tuple[str, ...] = ("person", "organization", "place", "project", "named_concept")
_RELATION_TYPES: tuple[str, ...] = (
    "created", "uses", "works_at", "member_of", "owns", "located_at",
    "participated_in", "about", "supports", "opposes", "part_of", "influenced",
)
_EVENT_TYPES: tuple[str, ...] = (
    "purchase", "employment_change", "relationship_change", "relocation",
    "health_event", "legal_event", "milestone", "other",
)
_DECISION_STATES: tuple[str, ...] = (
    "considering", "decided", "implemented", "abandoned", "reversed",
)
_OPEN_LOOP_STATES: tuple[str, ...] = ("opened", "continued", "resolved", "abandoned")

_TEXT_PLACEHOLDER = "%%UNIT_TEXT%%"

# Built with plain string substitution (not str.format) for the interactions/entity_kinds/etc.
# fields, and a non-format placeholder for the unit text -- the JSON schema example below has its
# own literal braces, which would collide with str.format's own escaping (a real bug this project's
# own Phase 10 exit tests caught: a second, runtime .format(text=...) call over a string that
# already went through one .format() pass raised KeyError on the JSON example's un-escaped braces).
_PROMPT_TEMPLATE = (
    """You are extracting structured information from a message. Respond with ONLY a single JSON object matching this schema (no markdown, no extra commentary):

{
  "interaction_primary": one of [__INTERACTIONS__, none],
  "interaction_secondary": same list or none (must differ from primary),
  "salience": one of [low, medium, high],
  "entities": array of {"mention": <exact substring from the text>, "kind": one of [__ENTITY_KINDS__]},
  "relations": array of {"subject_slot": <index into entities>, "relation_type": one of [__RELATION_TYPES__], "object_slot": <index into entities>, "evidence_mode": one of [explicit, implied]},
  "event": {"type": one of [__EVENT_TYPES__, none], "summary": <short phrase>, "actor_slot": <entity index or null>, "object_slot": <entity index or null>, "location_slot": <entity index or null>, "time_expression": <literal date phrase from text, or empty string>},
  "decision": {"state": one of [none, __DECISION_STATES__], "summary": <short phrase or empty>},
  "open_loop": {"state": one of [none, __OPEN_LOOP_STATES__], "summary": <short phrase or empty>}
}

Text:
\"\"\"
"""
    + _TEXT_PLACEHOLDER
    + """
\"\"\"

JSON:"""
)
for _placeholder, _values in (
    ("__INTERACTIONS__", _INTERACTION_LABELS),
    ("__ENTITY_KINDS__", _ENTITY_KINDS),
    ("__RELATION_TYPES__", _RELATION_TYPES),
    ("__EVENT_TYPES__", _EVENT_TYPES),
    ("__DECISION_STATES__", _DECISION_STATES),
    ("__OPEN_LOOP_STATES__", _OPEN_LOOP_STATES),
):
    _PROMPT_TEMPLATE = _PROMPT_TEMPLATE.replace(_placeholder, ", ".join(_values))
del _placeholder, _values


def _enum_rule(name: str, values: tuple[str, ...]) -> str:
    return f'{name} ::= ' + " | ".join(f'"\\"{v}\\""' for v in values)


# Every production is written on one line -- see module docstring on the multi-line grammar-parser
# quirk the 2026-09-27 benchmark found. PRODUCTION_STRING supports real JSON escaping (unlike the
# benchmark's throwaway simplified version), confirmed to parse on the same llama.cpp build once
# written this way.
_GRAMMAR_SOURCE = "\n".join(
    [
        'root ::= "{" ws "\\"interaction_primary\\"" ws ":" ws interaction-enum ws "," ws "\\"interaction_secondary\\"" ws ":" ws interaction-enum ws "," ws "\\"salience\\"" ws ":" ws salience-enum ws "," ws "\\"entities\\"" ws ":" ws entities-array ws "," ws "\\"relations\\"" ws ":" ws relations-array ws "," ws "\\"event\\"" ws ":" ws event-obj ws "," ws "\\"decision\\"" ws ":" ws decision-obj ws "," ws "\\"open_loop\\"" ws ":" ws open-loop-obj ws "}"',
        # Bounded, not "*" (unbounded) -- an untrained/random-weight test model (this project's own
        # exit-test methodology, matching annotation.py's) can get stuck sampling whitespace
        # indefinitely when a grammar rule allows unbounded repetition at dozens of insertion
        # points; a real trained model never needs more than a token or two of formatting
        # whitespace between JSON tokens, so this loses nothing for production and removes a real
        # pathological-generation path found by this phase's own exit tests.
        "ws ::= [ \\t\\n]{0,2}",
        # [^"\\\x00-\x1f] (not just [^"\\]) -- a bare, unescaped control character (e.g. a literal
        # newline byte) inside a JSON string is invalid per spec and json.loads correctly rejects
        # it; this project's own exit test 2 caught the omission live against a real model call.
        # A raw string here (not the usual doubled-backslash style) so the backslash count maps
        # 1:1 onto the actual GBNF source characters, after an earlier version of this exact line
        # miscounted escaping levels and silently produced the wrong number of backslashes.
        # {0,300} -- bounded, not unbounded: a genuinely reasonable ceiling for a short mention or
        # summary phrase, and (like ws above) removes a pathological-generation path an untrained
        # test model can otherwise get stuck in indefinitely repeating the "not a quote" branch.
        r'string ::= "\"" ([^"\\\x00-\x1f] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])){0,300} "\""',
        "int ::= [0-9] [0-9]?",
        _enum_rule("interaction-enum", (*_INTERACTION_LABELS, "none")),
        'salience-enum ::= "\\"low\\"" | "\\"medium\\"" | "\\"high\\""',
        _enum_rule("entity-kind-enum", _ENTITY_KINDS),
        'entity-obj ::= "{" ws "\\"mention\\"" ws ":" ws string ws "," ws "\\"kind\\"" ws ":" ws entity-kind-enum ws "}"',
        # {0,19} after the optional first element -- 0 to 20 entities total, well above the real
        # 2026-09-27 benchmark's observed max of 10 on a 273-unit stratified sample (decision 9);
        # a generous, not tight, ceiling, chosen to remove unbounded-repetition as a pathological
        # generation path (found live by this phase's own exit tests) without acting as a real
        # information-loss ceiling for genuinely entity-dense text.
        'entities-array ::= "[" ws (entity-obj (ws "," ws entity-obj){0,19})? ws "]"',
        _enum_rule("relation-type-enum", _RELATION_TYPES),
        'evidence-mode-enum ::= "\\"explicit\\"" | "\\"implied\\""',
        'relation-obj ::= "{" ws "\\"subject_slot\\"" ws ":" ws int ws "," ws "\\"relation_type\\"" ws ":" ws relation-type-enum ws "," ws "\\"object_slot\\"" ws ":" ws int ws "," ws "\\"evidence_mode\\"" ws ":" ws evidence-mode-enum ws "}"',
        # {0,14}: 0 to 15 relations total, same generous-margin-above-observed-max reasoning as
        # entities-array above (real benchmark max was 8).
        'relations-array ::= "[" ws (relation-obj (ws "," ws relation-obj){0,14})? ws "]"',
        _enum_rule("event-type-enum", (*_EVENT_TYPES, "none")),
        'slot-or-null ::= int | "null"',
        'event-obj ::= "{" ws "\\"type\\"" ws ":" ws event-type-enum ws "," ws "\\"summary\\"" ws ":" ws string ws "," ws "\\"actor_slot\\"" ws ":" ws slot-or-null ws "," ws "\\"object_slot\\"" ws ":" ws slot-or-null ws "," ws "\\"location_slot\\"" ws ":" ws slot-or-null ws "," ws "\\"time_expression\\"" ws ":" ws string ws "}"',
        _enum_rule("decision-state-enum", ("none", *_DECISION_STATES)),
        'decision-obj ::= "{" ws "\\"state\\"" ws ":" ws decision-state-enum ws "," ws "\\"summary\\"" ws ":" ws string ws "}"',
        _enum_rule("open-loop-state-enum", ("none", *_OPEN_LOOP_STATES)),
        'open-loop-obj ::= "{" ws "\\"state\\"" ws ":" ws open-loop-state-enum ws "," ws "\\"summary\\"" ws ":" ws string ws "}"',
        "",
    ]
)


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compute_model_ref() -> dict:
    path = os.environ.get(_MODEL_PATH_ENV)
    weights_hash = _file_sha256(path) if path and Path(path).is_file() else "unconfigured"
    return {
        "model_id": path or None,
        "weights_sha256": weights_hash,
        "prompt_hash": hashlib.sha256(_PROMPT_TEMPLATE.encode()).hexdigest(),
        "grammar_hash": hashlib.sha256(_GRAMMAR_SOURCE.encode()).hexdigest(),
        # Both change real output: _MAX_TOKENS decides which units truncate, and together they set
        # _bounded_prompt's input budget.
        "max_tokens": _MAX_TOKENS,
        "n_ctx": _N_CTX,
    }


_model = None
_grammar = None


def _get_model():
    global _model, _grammar
    if _model is None:
        path = os.environ.get(_MODEL_PATH_ENV)
        if not path or not Path(path).is_file():
            raise RuntimeError(
                f"{_MODEL_PATH_ENV} is not set to a real .gguf file -- no extraction model is "
                "configured yet (PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md decision 2)"
            )
        from llama_cpp import Llama, LlamaGrammar

        n_gpu_layers = int(os.environ.get(_GPU_LAYERS_ENV, "0"))
        _model = Llama(
            model_path=path,
            n_ctx=_N_CTX,
            logits_all=True,
            verbose=False,
            n_gpu_layers=n_gpu_layers,
        )
        _grammar = LlamaGrammar.from_string(_GRAMMAR_SOURCE)
    return _model, _grammar


def _bounded_prompt(model, unit_text: str) -> str:
    overhead = len(model.tokenize(_PROMPT_TEMPLATE.replace(_TEXT_PLACEHOLDER, "").encode("utf-8")))
    budget = _N_CTX - _MAX_TOKENS - _SAFETY_MARGIN - overhead
    text_tokens = model.tokenize(unit_text.encode("utf-8"), add_bos=False)
    if len(text_tokens) > budget:
        unit_text = model.detokenize(text_tokens[:budget]).decode("utf-8", errors="ignore")
    return _PROMPT_TEMPLATE.replace(_TEXT_PLACEHOLDER, unit_text)


def _none_if_literal_none(value):
    return None if value == "none" else value


_ROW_FIELDS: tuple[str, ...] = (
    "interaction_primary", "interaction_secondary", "salience", "entity_mentions",
    "relation_candidates", "event_type", "event_summary", "event_actor_slot", "event_object_slot",
    "event_location_slot", "event_time_expression", "decision_state", "decision_summary",
    "open_loop_state", "open_loop_summary",
)


def _build_row(
    text_unit_id: int, envelope_text: str, epoch_id: int, hit_token_limit: bool = False
) -> dict:
    """The pure envelope-JSON-text -> generative.extraction-row mapping, split out from
    _extract_one so the real rollout's orchestration script (which calls a remote llama.cpp
    server over HTTP for real throughput, not this module's own single-sequence _get_model())
    can reuse this exact logic rather than risk a second, independently-drifting copy -- the same
    class of mistake that already produced two real bugs today (clustering.py's n_jobs, and this
    module's own grammar/prompt fixes) when a benchmark script and its production counterpart
    were never reconciled.

    An envelope cut off by the token cap becomes an explicit 'truncated' row (migration 024)
    rather than an exception, which would fail the unit's whole source-month partition. Invalid
    JSON *without* hitting the cap still raises: the grammar should make that impossible.
    """
    try:
        envelope = json.loads(envelope_text)
    except json.JSONDecodeError:
        if not hit_token_limit:
            raise
        return {
            "text_unit_id": text_unit_id,
            **dict.fromkeys(_ROW_FIELDS),
            "extraction_status": "truncated",
            "computed_epoch_id": epoch_id,
        }

    event = envelope["event"]
    decision = envelope["decision"]
    open_loop = envelope["open_loop"]
    interaction_primary = _none_if_literal_none(envelope["interaction_primary"])
    interaction_secondary = _none_if_literal_none(envelope["interaction_secondary"])
    # The grammar can't forbid repeating the primary label as the secondary (100 of 69,959 real
    # extractions did); a repeat adds no information, so it maps to "no distinct secondary"
    # rather than violating extraction_check and failing the whole partition.
    if interaction_secondary == interaction_primary:
        interaction_secondary = None
    return {
        "text_unit_id": text_unit_id,
        # decision 6/10's schema has no 'none' literal for these two -- the model's "none" means
        # "no label", represented as SQL NULL, not the string "none" (unlike decision/open_loop
        # state below, whose CHECK constraints explicitly include 'none' as a real value).
        "interaction_primary": interaction_primary,
        "interaction_secondary": interaction_secondary,
        "salience": envelope["salience"],
        "entity_mentions": json.dumps(envelope["entities"]),
        "relation_candidates": json.dumps(envelope["relations"]),
        "event_type": _none_if_literal_none(event["type"]),
        "event_summary": event["summary"] or None,
        "event_actor_slot": event["actor_slot"],
        "event_object_slot": event["object_slot"],
        "event_location_slot": event["location_slot"],
        "event_time_expression": event["time_expression"] or None,
        "decision_state": decision["state"],
        "decision_summary": decision["summary"] or None,
        "open_loop_state": open_loop["state"],
        "open_loop_summary": open_loop["summary"] or None,
        "extraction_status": "complete",
        "computed_epoch_id": epoch_id,
    }


def _extract_one(text_unit_id: int, unit_text: str, epoch_id: int) -> dict:
    model, grammar = _get_model()
    prompt = _bounded_prompt(model, unit_text)
    output = model(prompt, grammar=grammar, max_tokens=_MAX_TOKENS, temperature=0.0)
    choice = output["choices"][0]
    return _build_row(
        text_unit_id, choice["text"], epoch_id, hit_token_limit=choice["finish_reason"] == "length"
    )


def _clear_extraction(connection: Connection, partition_key: str) -> None:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        return
    connection.execute(
        text(
            """
            DELETE FROM generative.extraction
            WHERE text_unit_id IN (
                SELECT id FROM semantic.text_unit
                WHERE source = :source AND started_at >= :start AND started_at < :end
            )
            """
        ),
        {"source": source, "start": start, "end": end},
    )


@deriver(
    name="generative_extract_text_unit",
    partition_class="LOCAL",
    final_inference="SEMANTIC",
    description=(
        "Single consolidated generative pass over semantic.text_unit: interaction labels, "
        "salience, entity/relation candidates, event/decision/open-loop state "
        "(PHASE10_LIFE_GRAPH_IMPLEMENTATION_PLAN.md decision 1)."
    ),
    output_relation="generative.extraction",
    inputs=("semantic_text_unit",),
    # 2: _build_row maps a secondary label that repeats the primary to NULL.
    code_version="2",
    model_ref=_compute_model_ref(),
    clear_partition=_clear_extraction,
)
def generative_extract_text_unit(
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

    rows = [_extract_one(u.id, u.text, epoch_id) for u in units]
    return ComputeResult(input_fingerprint=fingerprint, rows=rows)
