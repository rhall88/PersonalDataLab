"""Phase 6's semantic layer (PHASE6_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md): text units grouped
from canonical conversational events, and their local embeddings.

`semantic_text_unit` (HALO, no inputs -- a root derivation off canonical evidence, same tier as
`source_activity_daily`/`measure_coverage`) implements decision 2's three construction rules, one
per source's actual shape: 45-minute-gap sessions per `conversation_id` for facebook/imessages,
the provider's own `conversation_id` as the whole unit for openai/anthropic (a real conversation
boundary already exists; inventing a second grouping rule inside one would be redundant), and one
unit per message for google mail (no reliable thread grouping exists in this canonical schema,
and a time-gap heuristic doesn't fit email's asynchronous shape).

`semantic_embedding_bge_small` (LOCAL, `inputs=("semantic_text_unit",)`, 1:1 partition mapping)
embeds that partition's text units with the pinned `BAAI/bge-small-en-v1.5` model via `fastembed`
(ONNX Runtime, fully local, no PyTorch/CUDA stack). Like every other clear-and-replace deriver in
this codebase (`signal_anomaly`/`signal_change_point` fully recompute their entire GLOBAL history
on any change, for example), a change anywhere in a (source, month) partition re-embeds that
whole partition's text units, not just the ones that actually changed -- `semantic_text_unit`'s
own clear-and-replace regenerates `IDENTITY`-generated `text_unit.id` values on every real
re-run, so there is no stable content key across runs to embed incrementally against. This is the
established norm here, not a shortcut specific to this deriver; a real cost worth knowing, not
solved by inventing new machinery for this phase alone.

The embedding model is loaded once per worker process (module-level lazy singleton), not once per
partition -- `derive_run` processes every dirty partition in one Python process, and loading the
ONNX session costs real time relative to embedding a partition's handful of text units.
"""

from __future__ import annotations

import itertools
from datetime import UTC, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from ...fingerprints import fingerprint_value
from ..partitions import parse_local_key
from ..registry import ComputeResult, deriver

_SESSION_GAP = timedelta(minutes=45)
_MAX_CHARS = 8_000
_EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_EMBEDDING_DIMENSIONS = 384

# Decision 2: which real event_type(s) count as conversational content per source, and which of
# the three construction rules applies. Grounded directly against the real database (see the
# plan's Grounding section) -- not a generic "every source with text" rule.
_SOURCE_EVENT_TYPES: dict[str, tuple[str, ...]] = {
    "facebook": ("message",),
    "imessages": ("message.incoming", "message.outgoing"),
    # The device's own Messages database, holding only what the CSV source lacks (see
    # ios_backup/messages.py). Its own source, so no existing imessages partition changes.
    "ios_messages": ("message.incoming", "message.outgoing"),
    "openai": ("conversation_message",),
    "anthropic": ("conversation_message",),
    "google": ("mail_message",),
}
_SESSION_GROUPED_SOURCES = frozenset({"facebook", "imessages", "ios_messages"})
_WHOLE_CONVERSATION_SOURCES = frozenset({"openai", "anthropic"})
_PER_MESSAGE_SOURCES = frozenset({"google"})

_model = None  # module-level lazy singleton, see module docstring


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        _model = TextEmbedding(model_name=_EMBEDDING_MODEL_NAME)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of strings with the pinned model, returning plain Python float lists (not
    numpy arrays) -- callers pass these straight through to a `vector` column as a literal
    string, matching how this project's generic worker.py writes every other column: a plain
    parameterized value, no special-cased type handling.
    """
    if not texts:
        return []
    vectors = [vec.tolist() for vec in _get_model().embed(texts)]
    assert all(len(v) == _EMBEDDING_DIMENSIONS for v in vectors), (
        f"expected {_EMBEDDING_DIMENSIONS}-dimensional vectors from {_EMBEDDING_MODEL_NAME}, "
        "got a different width -- the pinned model or its ONNX export changed unexpectedly"
    )
    return vectors


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(repr(v) for v in values) + "]"


def _clear_text_unit(connection: Connection, partition_key: str) -> None:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        return
    connection.execute(
        text(
            "DELETE FROM semantic.text_unit WHERE source = :source "
            "AND started_at >= :start AND started_at < :end"
        ),
        {"source": source, "start": start, "end": end},
    )


_CHUNK_CHARS = 6_000
# Recordings enter this message layer only for the device-backup source: its Voice Memos and
# voicemail (the phone's own transcripts, stored as record text) and its Whisper transcripts. Scoped
# to one source on purpose -- no other source's partitions change, so the 70,145 existing units
# (and everything extracted from them) are untouched and no code_version bump is needed.
_AUDIO_UNIT_SOURCES = frozenset({"ios_backup"})


def chunk_text(body: str, limit: int = _CHUNK_CHARS) -> list[str]:
    """Split long text into pieces of at most `limit` characters, breaking at the last sentence
    end (or failing that, whitespace) inside each window, so a three-hour recording becomes
    several whole-sentence units instead of one cut off at the size cap."""
    body = body.strip()
    chunks: list[str] = []
    while len(body) > limit:
        window = body[:limit]
        cut = max(window.rfind(mark) for mark in (". ", "? ", "! ", "\n"))
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < limit // 2:
            cut = limit - 1
        chunks.append(body[: cut + 1].strip())
        body = body[cut + 1 :].strip()
    if body:
        chunks.append(body)
    return chunks


def _audio_units(connection: Connection, source: str, start, end) -> tuple[bytes, list[dict]]:
    window_start, window_end = start - timedelta(days=1), end + timedelta(days=1)
    rows = connection.execute(
        text(
            """
            SELECT rec.id AS record_id, NULL::bigint AS asset_id, rec.occurred_at AS at, rec.text,
                   (rec.metadata->>'duration_seconds')::float AS seconds
            FROM records rec
            WHERE rec.source = :source AND rec.record_type IN ('voice_memo', 'voicemail')
              AND rec.lifecycle_state = 'active' AND rec.text IS NOT NULL AND rec.text <> ''
              AND rec.occurred_at >= :window_start AND rec.occurred_at < :window_end
            UNION ALL
            SELECT NULL, a.id, COALESCE(a.captured_at, a.created_at), t.text, t.duration_seconds
            FROM media.transcript t JOIN assets a ON a.id = t.asset_id
            WHERE a.source = :source AND a.lifecycle_state = 'active'
              AND t.status = 'succeeded' AND t.text <> ''
              AND NOT EXISTS (SELECT 1 FROM media.audio_supersession s WHERE s.asset_id = a.id)
              AND COALESCE(a.captured_at, a.created_at) >= :window_start
              AND COALESCE(a.captured_at, a.created_at) < :window_end
            ORDER BY at, record_id NULLS LAST, asset_id
            """
        ),
        {"source": source, "window_start": window_start, "window_end": window_end},
    ).all()
    fingerprint = fingerprint_value(
        [[r.record_id, r.asset_id, r.at.isoformat(), r.text] for r in rows]
    )
    units = []
    for r in rows:
        if not (start <= r.at.astimezone(UTC).date() < end):
            continue
        ended = r.at + timedelta(seconds=r.seconds) if r.seconds else r.at
        for index, chunk in enumerate(chunk_text(r.text)):
            units.append({
                "source": source, "conversation_id": None, "event_ids": [], "started_at": r.at,
                "ended_at": ended, "text": chunk, "truncated": False, "record_id": r.record_id,
                "asset_id": r.asset_id, "chunk_index": index,
            })
    return fingerprint, units


def _build_units(source: str, events: list) -> list[dict]:
    if source in _PER_MESSAGE_SOURCES:
        return [_make_unit(source, e.conversation_id, [e]) for e in events]

    by_conversation: dict[int | None, list] = {}
    for e in events:
        by_conversation.setdefault(e.conversation_id, []).append(e)

    if source in _WHOLE_CONVERSATION_SOURCES:
        return [_make_unit(source, conv_id, msgs) for conv_id, msgs in by_conversation.items()]

    # Session-grouped sources (facebook, imessages): split each conversation's messages into
    # sessions wherever consecutive messages are more than _SESSION_GAP apart. The tiebreaker on
    # `m.id` (not just `m.occurred_at`) matters for real data: two messages in the same
    # conversation can share an identical timestamp, and without a deterministic tiebreaker the
    # concatenation order -- and therefore the resulting text -- depends on whatever order the
    # SQL query happened to return them in, which is not guaranteed stable across two physically
    # different copies of the same logical rows (found via the disposability rebuild: 12 of
    # 70,145 real text units differed after a full wipe+rebuild against a restored backup, all
    # tied-timestamp cases).
    units: list[dict] = []
    for conv_id, msgs in by_conversation.items():
        msgs = sorted(msgs, key=lambda m: (m.occurred_at, m.id))
        current = [msgs[0]]
        for prev, cur in itertools.pairwise(msgs):
            if cur.occurred_at - prev.occurred_at > _SESSION_GAP:
                units.append(_make_unit(source, conv_id, current))
                current = []
            current.append(cur)
        units.append(_make_unit(source, conv_id, current))
    return units


def _make_unit(source: str, conversation_id, msgs: list) -> dict:
    joined = "\n\n".join(m.text for m in msgs)
    truncated = len(joined) > _MAX_CHARS
    return {
        "source": source,
        "conversation_id": conversation_id,
        "event_ids": [m.id for m in msgs],
        "started_at": msgs[0].occurred_at,
        "ended_at": msgs[-1].occurred_at,
        "text": joined[:_MAX_CHARS] if truncated else joined,
        "truncated": truncated,
    }


@deriver(
    name="semantic_text_unit",
    partition_class="HALO",
    lookback_halo="1 days",
    lookahead_halo="1 days",
    final_inference="COMPUTED",
    description=(
        "Groups conversational events into text units per source's own shape "
        "(PHASE6_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md decision 2)."
    ),
    output_relation="semantic.text_unit",
    clear_partition=_clear_text_unit,
    # v2: added an `id` tiebreaker to the event ordering (see _build_units) -- a real
    # disposability-rebuild test found 12/70,145 real text units whose concatenated text
    # depended on tied-timestamp event order, which isn't guaranteed stable across two
    # physical copies of the same rows. Bumped for the audit trail (this is a real behavior
    # change, worth a recorded reason); it does not by itself defer the fix -- the worker calls
    # whatever compute() is currently imported for a partition's still-active version_id, so this
    # fix takes effect on that partition's next real recompute regardless of promote status.
    code_version="2",
)
def semantic_text_unit(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        return ComputeResult(input_fingerprint=fingerprint_value(["undated"]), rows=[])
    if source in _AUDIO_UNIT_SOURCES:
        fingerprint, units = _audio_units(connection, source, start, end)
        return ComputeResult(
            input_fingerprint=fingerprint, rows=[{**u, "computed_epoch_id": epoch_id} for u in units]
        )
    event_types = _SOURCE_EVENT_TYPES.get(source)
    if not event_types:
        return ComputeResult(input_fingerprint=fingerprint_value(["no-event-types"]), rows=[])

    # A 1-day halo on each side is enough for any 45-minute-gap decision near the month
    # boundary to see every message it needs to -- the gap test only ever compares two
    # consecutive messages, and 1 day is far wider than the 45-minute threshold it's checking.
    window_start = start - timedelta(days=1)
    window_end = end + timedelta(days=1)
    events = connection.execute(
        text(
            """
            SELECT id, conversation_id, occurred_at, text
            FROM events
            WHERE source = :source AND event_type = ANY(:event_types)
              AND text IS NOT NULL AND text <> ''
              AND occurred_at >= :window_start AND occurred_at < :window_end
            ORDER BY conversation_id NULLS FIRST, occurred_at, id
            """
        ),
        {
            "source": source, "event_types": list(event_types),
            "window_start": window_start, "window_end": window_end,
        },
    ).all()
    fingerprint = fingerprint_value([[e.id, e.occurred_at.isoformat()] for e in events])

    rows = []
    for unit in _build_units(source, events):
        # Same "UTC calendar day" convention activity.py's own module docstring documents,
        # applied here explicitly in Python (rather than at the SQL level, since this is a
        # decision about a *constructed* unit, not a raw column) so this doesn't silently
        # depend on the session/driver's default timezone handling.
        if not (start <= unit["started_at"].astimezone(UTC).date() < end):
            continue
        rows.append({**unit, "computed_epoch_id": epoch_id})
    return ComputeResult(input_fingerprint=fingerprint, rows=rows)


def _clear_embedding(connection: Connection, partition_key: str) -> None:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        return
    connection.execute(
        text(
            """
            DELETE FROM semantic.embedding_bge_small_en_v15
            WHERE text_unit_id IN (
                SELECT id FROM semantic.text_unit
                WHERE source = :source AND started_at >= :start AND started_at < :end
            )
            """
        ),
        {"source": source, "start": start, "end": end},
    )


@deriver(
    name="semantic_embedding_bge_small",
    partition_class="LOCAL",
    final_inference="COMPUTED",
    description=(
        f"Embeds semantic.text_unit rows with the pinned {_EMBEDDING_MODEL_NAME} model "
        "(PHASE6_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md decision 3), fully local via fastembed."
    ),
    output_relation="semantic.embedding_bge_small_en_v15",
    inputs=("semantic_text_unit",),
    clear_partition=_clear_embedding,
)
def semantic_embedding_bge_small(
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

    vectors = embed_texts([u.text for u in units])
    rows = [
        {"text_unit_id": u.id, "vector": _vector_literal(vec)}
        for u, vec in zip(units, vectors)
    ]
    return ComputeResult(input_fingerprint=fingerprint, rows=rows)


def search(
    engine: Engine, query: str, *, limit: int = 10, source: str | None = None,
    include_documents: bool = False,
) -> list[dict]:
    """Embed `query` with the same pinned model and rank semantic.text_unit by cosine distance
    (pgvector's `<=>` operator). A lookup tool, not a statistical finding (architecture section
    8 step 9: vectors only ever fetch examples, statistics decide what matters) -- this returns
    text units with their provenance, never a confidence profile or a know.claim row.

    include_documents=True (PHASE8_DOCUMENT_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md decision 2) also
    ranks semantic.text_unit_document (records -- Drive documents, NotebookLM, Recorder
    transcripts, Apple Notes) and merges both result sets by distance -- an explicit opt-in, so
    this function's already-verified message-search behavior (Phase 6's real rollout) is
    unchanged by default. Each row carries `kind` ("message" or "document") since the two sources
    have different provenance shapes (conversation_id/event_ids vs. record_id).
    """
    [vector] = embed_texts([query])
    query_clause = "AND tu.source = :source" if source is not None else ""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                f"""
                SELECT 'message' AS kind, tu.id, tu.source, tu.conversation_id, tu.event_ids,
                       tu.record_id, tu.asset_id, tu.chunk_index, tu.started_at, tu.ended_at, tu.text, tu.truncated,
                       (e.vector <=> CAST(:vector AS vector)) AS distance
                FROM semantic.embedding_bge_small_en_v15 e
                JOIN semantic.text_unit tu ON tu.id = e.text_unit_id
                WHERE TRUE {query_clause}
                ORDER BY distance ASC
                LIMIT :limit
                """
            ),
            {"vector": _vector_literal(vector), "source": source, "limit": limit},
        ).mappings().all()
        results = [dict(row) for row in rows]

        if include_documents:
            doc_query_clause = "AND tu.source = :source" if source is not None else ""
            doc_rows = connection.execute(
                text(
                    f"""
                    SELECT 'document' AS kind, tu.id, tu.source, tu.record_id, tu.asset_id,
                           tu.chunk_index, tu.started_at,
                           tu.ended_at, tu.text, tu.truncated,
                           (e.vector <=> CAST(:vector AS vector)) AS distance
                    FROM semantic.embedding_bge_small_en_v15_document e
                    JOIN semantic.text_unit_document tu ON tu.id = e.text_unit_document_id
                    WHERE TRUE {doc_query_clause}
                    ORDER BY distance ASC
                    LIMIT :limit
                    """
                ),
                {"vector": _vector_literal(vector), "source": source, "limit": limit},
            ).mappings().all()
            results.extend(dict(row) for row in doc_rows)

    results.sort(key=lambda row: row["distance"])
    return results[:limit]
