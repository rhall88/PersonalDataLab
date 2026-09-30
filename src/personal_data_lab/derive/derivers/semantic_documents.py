"""Phase 8's document semantic layer (PHASE8_DOCUMENT_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md):
brings `records`' genuinely prose-like content into the same semantic layer `semantic-search`/
`semantic_embedding_bge_small` already read from `events` -- Drive documents, NotebookLM
notebooks/sources, Recorder transcripts, Apple Notes. Deliberately a separate module from
`semantic.py` (decision 2): a `records`-sourced unit isn't built the same way an `events`-sourced
one is (no session-grouping needed -- a record is already one coherent document), and sharing
`semantic.text_unit`'s `event_ids` column for a concept it was never designed for would be exactly
the ambiguous shared-column design this codebase's provenance-citation discipline argues against.

`semantic_text_unit_document` (HALO root, no inputs -- same tier as `semantic_text_unit` itself)
reads `records` filtered to `_NAMESPACE_ALLOWLIST`, the grounded set of namespaces that are real
prose rather than event-log noise (Drive Activity CSVs, account/device/purchase metadata, etc. --
see the plan's grounding). `semantic_embedding_document_bge_small` (LOCAL, inputs=
("semantic_text_unit_document",)) embeds them with the same pinned `BAAI/bge-small-en-v1.5`
model via `semantic.py`'s own `embed_texts()` -- no second copy of the model, no new embedding
space.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ...fingerprints import fingerprint_value
from ..partitions import parse_local_key
from ..registry import ComputeResult, deriver
from .semantic import _AUDIO_UNIT_SOURCES, _vector_literal, chunk_text, embed_texts

_MAX_CHARS = 8_000

# Grounded directly against the real database (PHASE8 plan's own grounding section) -- not a
# generic "every records row with text" rule. Each entry is a real, sampled, genuinely prose-like
# namespace; everything left out (drive/document/csv, mail/thread/mbox, account/device/purchase/
# etc. metadata, drive/document/xlsx's spreadsheet-cell-dump text) was excluded for a specific,
# documented reason in the plan, not by omission.
_NAMESPACE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "google_takeout/notebooklm/document/html",
        "google_takeout/notebooklm/document/md",
        "google_takeout/notebooklm/notebook/json",
        "google_takeout/drive/document/html",
        "google_takeout/drive/document/docx",
        "google_takeout/drive/document/txt",
        "google_takeout/recorder/document/txt",
        "google_takeout/gemini/conversation/html",
        "google_takeout/gemini_workspace/conversation/txt",
        "apple_privacy/notes/note/record",
    }
)
# (The iPhone backup's Voice Memos and voicemail records were briefly listed here; since v2 they
# are text units in the message layer instead -- semantic.py's _AUDIO_UNIT_SOURCES -- so they get
# extraction and topics, and are not stored twice.)


def _document_units(source: str, at, body: str, *, record_id=None, asset_id=None) -> list[dict]:
    """One unit per chunk (v2): a long document or transcript is split at sentence boundaries
    instead of being cut off at _MAX_CHARS, so all of it is searchable."""
    return [
        {"source": source, "record_id": record_id, "asset_id": asset_id, "chunk_index": index,
         "started_at": at, "ended_at": at, "text": chunk, "truncated": False}
        for index, chunk in enumerate(chunk_text(body, _MAX_CHARS))
    ]


# Whisper transcripts of kept recordings (media.transcript), for every source whose audio does
# not already go to the message layer. Most have no capture date (the Google Takeout recordings),
# so they live in the source's undated partition.
_TRANSCRIPTS_SQL = """
    SELECT a.id, t.text, COALESCE(a.captured_at, a.created_at) AS at
    FROM media.transcript t JOIN assets a ON a.id = t.asset_id
    WHERE a.source = :source AND a.lifecycle_state = 'active'
      AND t.status = 'succeeded' AND t.text <> ''
      AND NOT EXISTS (SELECT 1 FROM media.audio_supersession s WHERE s.asset_id = a.id)
      AND {window}
    ORDER BY a.id
"""


def _transcripts(connection: Connection, source: str, window: str, params: dict) -> list:
    if source in _AUDIO_UNIT_SOURCES:
        return []
    return connection.execute(
        text(_TRANSCRIPTS_SQL.format(window=window)), {"source": source, **params}
    ).all()


def _clear_text_unit_document(connection: Connection, partition_key: str) -> None:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        connection.execute(
            text(
                "DELETE FROM semantic.text_unit_document "
                "WHERE source = :source AND started_at IS NULL"
            ),
            {"source": source},
        )
        return
    connection.execute(
        text(
            "DELETE FROM semantic.text_unit_document WHERE source = :source "
            "AND started_at >= :start AND started_at < :end"
        ),
        {"source": source, "start": start, "end": end},
    )


@deriver(
    name="semantic_text_unit_document",
    partition_class="HALO",
    lookback_halo="1 days",
    lookahead_halo="1 days",
    final_inference="COMPUTED",
    description=(
        "Groups records' prose-like content (Drive documents, NotebookLM, Recorder transcripts, "
        "Apple Notes) into one text unit per record "
        "(PHASE8_DOCUMENT_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md decision 1)."
    ),
    output_relation="semantic.text_unit_document",
    clear_partition=_clear_text_unit_document,
    # 2: long documents become several chunked units instead of one cut off at _MAX_CHARS, and
    # Whisper transcripts (media.transcript) join as asset-backed units (migration 029).
    code_version="2",
)
def semantic_text_unit_document(
    connection: Connection, partition_key: str, epoch_id: int
) -> ComputeResult:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        # 99.97% of the real in-scope population has no occurred_at/created_at/updated_at at all
        # (PHASE8 plan grounding) -- this is the primary case, not a rare edge, so it gets its own
        # real query rather than the "return empty" shortcut semantic_text_unit uses (that
        # shortcut is valid there only because Phase 6's events population always has a real
        # timestamp; media_transcribe_whisper's own rollout found exactly this gap the hard way).
        records = connection.execute(
            text(
                """
                SELECT id, text FROM records
                WHERE source = :source AND source_record_namespace = ANY(:namespaces)
                  AND text IS NOT NULL AND text <> ''
                  AND occurred_at IS NULL AND created_at IS NULL AND updated_at IS NULL
                ORDER BY id
                """
            ),
            {"source": source, "namespaces": list(_NAMESPACE_ALLOWLIST)},
        ).all()
        transcripts = _transcripts(
            connection, source, "COALESCE(a.captured_at, a.created_at) IS NULL", {}
        )
        fingerprint = fingerprint_value(
            [["r", r.id, r.text] for r in records] + [["a", t.id, t.text] for t in transcripts]
        )
        rows = [
            {**unit, "computed_epoch_id": epoch_id}
            for r in records
            for unit in _document_units(source, None, r.text, record_id=r.id)
        ] + [
            {**unit, "computed_epoch_id": epoch_id}
            for t in transcripts
            for unit in _document_units(source, None, t.text, asset_id=t.id)
        ]
        return ComputeResult(input_fingerprint=fingerprint, rows=rows)

    # A 1-day halo on each side (matching semantic_text_unit's own precedent) is generous enough
    # that a record whose fallback date sits right on a month boundary is never split by
    # partitioning alone, even though this deriver has no cross-record grouping decision to make.
    window_start = start - timedelta(days=1)
    window_end = end + timedelta(days=1)
    records = connection.execute(
        text(
            """
            SELECT id, text, COALESCE(occurred_at, created_at, updated_at) AS at
            FROM records
            WHERE source = :source AND source_record_namespace = ANY(:namespaces)
              AND text IS NOT NULL AND text <> ''
              AND COALESCE(occurred_at, created_at, updated_at) >= :window_start
              AND COALESCE(occurred_at, created_at, updated_at) < :window_end
            ORDER BY id
            """
        ),
        {
            "source": source, "namespaces": list(_NAMESPACE_ALLOWLIST),
            "window_start": window_start, "window_end": window_end,
        },
    ).all()
    transcripts = _transcripts(
        connection, source,
        "COALESCE(a.captured_at, a.created_at) >= :window_start "
        "AND COALESCE(a.captured_at, a.created_at) < :window_end",
        {"window_start": window_start, "window_end": window_end},
    )
    fingerprint = fingerprint_value(
        [["r", r.id, r.text] for r in records] + [["a", t.id, t.text] for t in transcripts]
    )

    rows = []
    for record in records:
        if start <= record.at.date() < end:
            rows += [{**u, "computed_epoch_id": epoch_id}
                     for u in _document_units(source, record.at, record.text, record_id=record.id)]
    for t in transcripts:
        if start <= t.at.date() < end:
            rows += [{**u, "computed_epoch_id": epoch_id}
                     for u in _document_units(source, t.at, t.text, asset_id=t.id)]
    return ComputeResult(input_fingerprint=fingerprint, rows=rows)


def _clear_embedding_document(connection: Connection, partition_key: str) -> None:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        connection.execute(
            text(
                """
                DELETE FROM semantic.embedding_bge_small_en_v15_document
                WHERE text_unit_document_id IN (
                    SELECT id FROM semantic.text_unit_document
                    WHERE source = :source AND started_at IS NULL
                )
                """
            ),
            {"source": source},
        )
        return
    connection.execute(
        text(
            """
            DELETE FROM semantic.embedding_bge_small_en_v15_document
            WHERE text_unit_document_id IN (
                SELECT id FROM semantic.text_unit_document
                WHERE source = :source AND started_at >= :start AND started_at < :end
            )
            """
        ),
        {"source": source, "start": start, "end": end},
    )


@deriver(
    name="semantic_embedding_document_bge_small",
    partition_class="LOCAL",
    final_inference="COMPUTED",
    description=(
        "Embeds semantic.text_unit_document rows with the same pinned BAAI/bge-small-en-v1.5 "
        "model semantic_embedding_bge_small uses "
        "(PHASE8_DOCUMENT_SEMANTIC_LAYER_IMPLEMENTATION_PLAN.md decision 3)."
    ),
    output_relation="semantic.embedding_bge_small_en_v15_document",
    inputs=("semantic_text_unit_document",),
    clear_partition=_clear_embedding_document,
)
def semantic_embedding_document_bge_small(
    connection: Connection, partition_key: str, epoch_id: int
) -> ComputeResult:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        units = connection.execute(
            text(
                "SELECT id, text FROM semantic.text_unit_document "
                "WHERE source = :source AND started_at IS NULL ORDER BY id"
            ),
            {"source": source},
        ).all()
    else:
        units = connection.execute(
            text(
                "SELECT id, text FROM semantic.text_unit_document "
                "WHERE source = :source AND started_at >= :start AND started_at < :end ORDER BY id"
            ),
            {"source": source, "start": start, "end": end},
        ).all()
    fingerprint = fingerprint_value([[u.id, u.text] for u in units])
    if not units:
        return ComputeResult(input_fingerprint=fingerprint, rows=[])

    vectors = embed_texts([u.text for u in units])
    rows = [
        {"text_unit_document_id": u.id, "vector": _vector_literal(vec)}
        for u, vec in zip(units, vectors)
    ]
    return ComputeResult(input_fingerprint=fingerprint, rows=rows)
