"""OCR/transcription (`ROADMAP.md` item 6, "Optional media enrichment"): local audio
transcription via `faster-whisper` (CTranslate2, fully local CPU inference, no PyTorch/CUDA --
the same dependency ethos Phase 6's embedding model already established). A LOCAL/HALO-style
root deriver keyed by `source:YYYY-MM` (via `COALESCE(captured_at, created_at)`, the exact same
date expression `changefeed.py`'s own `WATCHED_KINDS` already uses for assets), not GLOBAL: a
single-partition, all-470-files-in-one-transaction design would mean a crash partway through
loses every already-completed transcription, and this job is expected to run for a long time
unattended. Partitioning by month means each partition's transcriptions commit independently as
`derive-run` works through them -- a crash mid-run only loses the one in-flight partition, not
the whole corpus, and a resumed run naturally picks up wherever it left off via the ordinary
dirty-partition mechanism.

Deliberately built and shipped together (schema + runtime in one PR/migration) rather than this
project's usual plan-then-schema-then-runtime cadence, at Ryan's explicit request to move quickly
and get a long-running job started -- a conscious, one-off scope trade-off, not a new default for
future phases.

Ryan was told exactly what the real audio population contains before this ran: some of it is
intimate/personal recordings, at least one naming a real partner. He explicitly chose to
transcribe the full library with no exclusions (2026-09-25) -- see `PROJECT_STATE.md`. Not
filtered here by design, matching that direction; this module makes no content judgment of its
own, the same way `semantic_text_unit` doesn't judge message content either.
"""

from __future__ import annotations

import os
import re
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ...apple_export.inventory import AppleInventoryError, open_apple_locator
from ...facebook_export.inventory import FacebookInventoryError, open_facebook_locator
from ...fingerprints import fingerprint_value
from ..partitions import parse_local_key
from ..registry import ComputeResult, deriver

_WHISPER_MODEL_NAME = "small"

_model = None  # module-level lazy singleton, same precedent semantic.py's embedding model uses


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel(_WHISPER_MODEL_NAME, device="cpu", compute_type="int8")
    return _model


@contextmanager
def _open_zip_member(original_path: str) -> Iterator[BinaryIO]:
    """Open a `<zip path>!/<member path>` reference -- the convention google/openai/facebook's
    importers use for an asset whose bytes live inside a retained export ZIP, never extracted to
    disk on their own.
    """
    zip_path, _, member_path = original_path.partition("!/")
    with zipfile.ZipFile(zip_path) as archive, archive.open(member_path) as stream:
        yield stream


@contextmanager
def _open_asset_bytes(source: str, original_path: str) -> Iterator[BinaryIO]:
    """Apple and Facebook each have their own JSON locator format (`open_apple_locator`/
    `open_facebook_locator`, already built for exactly this in their own export packages --
    reused here rather than re-implemented); google/openai use the simpler `<zip>!/<member>`
    convention `_open_zip_member` handles; a path with neither is a plain file on disk.

    A real bug found in the first real rollout: this function originally had no Facebook case
    at all, so all 6 real Facebook audio assets fell through to the `!/`-based path and failed
    -- `original_path` for Facebook is the same kind of whole-locator JSON blob Apple's is, not a
    `!/`-splittable string, so there was never a "generic" path that could have covered it by
    accident.
    """
    if source == "apple":
        with open_apple_locator(original_path) as stream:
            yield stream
        return
    if source == "facebook":
        with open_facebook_locator(original_path) as stream:
            yield stream
        return
    if "!/" in original_path:
        with _open_zip_member(original_path) as stream:
            yield stream
        return
    with open(original_path, "rb") as stream:
        yield stream


def _clear_transcripts(connection: Connection, partition_key: str) -> None:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        connection.execute(
            text(
                "DELETE FROM media.transcript WHERE source = :source AND asset_id IN ("
                "SELECT id FROM assets WHERE source = :source "
                "AND COALESCE(captured_at, created_at) IS NULL)"
            ),
            {"source": source},
        )
        return
    connection.execute(
        text(
            "DELETE FROM media.transcript WHERE source = :source AND asset_id IN ("
            "SELECT id FROM assets WHERE source = :source "
            "AND COALESCE(captured_at, created_at) >= :start "
            "AND COALESCE(captured_at, created_at) < :end)"
        ),
        {"source": source, "start": start, "end": end},
    )


_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,5}$")


def _temp_file_suffix(filename: str) -> str:
    """A real bug the first real rollout found: deriving the temp file's suffix from
    `original_path` broke Apple and Facebook, whose `original_path` is a whole locator JSON blob,
    not a plain filename -- `Path(...).suffix` on that blob produced garbage like
    `.m4a",5]]}`, which Windows then rejected outright (`OSError: [Errno 22] Invalid argument`)
    for every single Apple/Facebook file, not just a few. `assets.filename` is always a plain,
    clean filename regardless of source-specific locator format -- use that instead, and validate
    the extracted suffix is actually a short, simple extension before trusting it, rather than
    assuming any given filename is well-formed.
    """
    suffix = Path(filename).suffix
    return suffix if _SAFE_SUFFIX.match(suffix) else ".audio"


def _transcribe_one(asset, epoch_id: int) -> dict:
    base = {
        "asset_id": asset.id, "source": asset.source, "model": _WHISPER_MODEL_NAME,
        "computed_epoch_id": epoch_id,
    }
    try:
        with _open_asset_bytes(asset.source, asset.original_path) as stream:
            data = stream.read()
    except (OSError, AppleInventoryError, FacebookInventoryError, zipfile.BadZipFile, KeyError) as error:
        return {
            **base, "language": None, "text": "", "duration_seconds": None,
            "status": "unreadable", "error": str(error)[:500],
        }

    suffix = _temp_file_suffix(asset.filename)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        segments, info = _get_model().transcribe(tmp_path)
        transcript_text = " ".join(segment.text.strip() for segment in segments).strip()
        return {
            **base, "language": info.language, "text": transcript_text,
            "duration_seconds": info.duration, "status": "succeeded", "error": None,
        }
    except Exception as error:  # noqa: BLE001 -- one bad file must record 'failed', never crash
        # the whole partition's worth of otherwise-good transcriptions.
        return {
            **base, "language": None, "text": "", "duration_seconds": None,
            "status": "failed", "error": str(error)[:500],
        }
    finally:
        if tmp_path is not None:
            os.unlink(tmp_path)


@deriver(
    name="media_transcribe_whisper",
    partition_class="LOCAL",
    final_inference="COMPUTED",
    description=(
        "Transcribes audio assets with a local faster-whisper model "
        "(ROADMAP.md item 6, OCR/transcription)."
    ),
    output_relation="media.transcript",
    clear_partition=_clear_transcripts,
)
def media_transcribe_whisper(connection: Connection, partition_key: str, epoch_id: int) -> ComputeResult:
    source, start, end = parse_local_key(partition_key)
    if start is None:
        rows = connection.execute(
            text(
                "SELECT id, source, original_path, filename FROM assets "
                "WHERE source = :source AND mime_type LIKE 'audio/%' "
                "AND lifecycle_state = 'active' AND COALESCE(captured_at, created_at) IS NULL "
                "ORDER BY id"
            ),
            {"source": source},
        ).all()
    else:
        rows = connection.execute(
            text(
                "SELECT id, source, original_path, filename FROM assets "
                "WHERE source = :source AND mime_type LIKE 'audio/%' "
                "AND lifecycle_state = 'active' "
                "AND COALESCE(captured_at, created_at) >= :start "
                "AND COALESCE(captured_at, created_at) < :end "
                "ORDER BY id"
            ),
            {"source": source, "start": start, "end": end},
        ).all()

    fingerprint = fingerprint_value([[row.id, row.original_path] for row in rows])
    if not rows:
        return ComputeResult(input_fingerprint=fingerprint, rows=[])

    output_rows = [_transcribe_one(row, epoch_id) for row in rows]
    return ComputeResult(input_fingerprint=fingerprint, rows=output_rows)
