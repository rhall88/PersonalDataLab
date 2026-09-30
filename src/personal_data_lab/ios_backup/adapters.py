"""Turn an inventoried iOS backup into ParsedExports.

Three shapes, all under source `ios_backup`:

* one ParsedExport per audio file, carrying just that file's asset (the file is its own source
  file, so every asset has byte-level provenance);
* one for Voice Memos' `CloudRecordings.db`: a `voice_memo` record per recording (title, date,
  duration) whose text is the on-device transcript iOS embeds in the `.m4a` itself (a `tsrp`
  atom holding JSON), plus a relationship to the recording's audio asset;
* one for the Phone app's `voicemail.db`: a `voicemail` record per message (caller, date,
  duration) whose text is Apple's transcript (`<ROWID>.transcript`, an NSKeyedArchiver
  `VMVoicemailTranscript`), plus a relationship to its `.amr` asset.

Provider transcripts are stored as record text, never as `media.transcript` rows -- that table
belongs to the Whisper deriver, which clears and rewrites it.
"""

from __future__ import annotations

import json
import plistlib
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from ..models import NormalizedAsset, NormalizedRecord, NormalizedRelationship, ParsedExport
from .inventory import (
    AUDIO_MIME,
    VOICE_MEMOS_DOMAIN,
    VOICEMAIL_DOMAIN,
    BackupFile,
    IosBackupInventory,
    _sha256,
)

SOURCE = "ios_backup"
# v2: voicemail re-deliveries collapse to one record; links follow identical-byte audio.
# v3: records and relationships carry the backup date as authority, so a later reading of the
#     device (or a corrected parser) updates them instead of conflicting.
# v4: same output as v3. The first real import (v1) lacked record authority, so v2/v3 conflicted
#     against it; its voicemail rows were removed and this version re-keys the import run.
PARSER_VERSION = "ios-backup-audio-v4"
CORE_DATA_EPOCH = 978307200  # 2001-01-01T00:00:00Z
VOICE_MEMO_NAMESPACE = "ios_backup/voice_memos/recording"
VOICEMAIL_NAMESPACE = "ios_backup/voicemail/message"
_CONTAINER_ATOMS = frozenset({"moov", "udta", "trak", "mdia", "minf", "stbl"})


def asset_namespace(product: str) -> str:
    return f"ios_backup/{product}"


def asset_id(item: BackupFile) -> str:
    return f"{item.domain}/{item.relative_path}"


def _export(item: BackupFile, inventory: IosBackupInventory, category: str, sha256: str, **content) -> ParsedExport:
    return ParsedExport(
        source=SOURCE,
        parser_version=PARSER_VERSION,
        category=category,
        filename=item.filename,
        original_path=item.path,
        sha256=sha256,
        size_bytes=item.size_bytes,
        exported_at=inventory.backup_date,
        export_id=f"{inventory.unique_device_id}:{inventory.manifest_sha256[:16]}",
        snapshot_kind="partial",
        snapshot_scope="device_backup_audio",
        **content,
    )


def _authority(inventory: IosBackupInventory) -> dict:
    if inventory.backup_date is None:
        return {"authority_kind": "unknown"}
    return {"authority_at": inventory.backup_date, "authority_kind": "exported_at"}


def _asset_ref(item: BackupFile, same_bytes: dict[str, tuple[str, str]] | None) -> dict[str, str]:
    """Where a relationship should point for `item`. A file whose bytes equal an earlier file's is
    short-circuited on import (its source file is already recorded), so no asset row exists under
    its own id; point at the row that does."""
    namespace, source_id = (same_bytes or {}).get(asset_id(item), (asset_namespace(item.product), asset_id(item)))
    return {"object_namespace": namespace, "object_source_id": source_id}


def audio_export(item: BackupFile, inventory: IosBackupInventory) -> ParsedExport:
    sha256 = _sha256(Path(item.path))
    asset = NormalizedAsset(
        source=SOURCE,
        asset_namespace=asset_namespace(item.product),
        source_asset_id=asset_id(item),
        sha256=sha256,
        mime_type=AUDIO_MIME[item.extension],
        filename=item.filename,
        original_path=item.path,
        size_bytes=item.size_bytes,
        created_at=item.birth,
        captured_at=item.birth or item.last_modified,
        modified_at=item.last_modified,
        metadata={"domain": item.domain, "relative_path": item.relative_path, "product": item.product,
                  "backup_file_id": item.file_id},
        authority_at=inventory.backup_date,
        authority_kind="exported_at" if inventory.backup_date else "unknown",
    )
    return _export(item, inventory, item.product, sha256, assets=(asset,))


# --- Voice Memos ------------------------------------------------------------------------------


def _atoms(stream, start: int, end: int) -> Iterator[tuple[str, int, int]]:
    """Yield (type, payload offset, payload size) for MP4 atoms, descending into containers."""
    position = start
    while position + 8 <= end:
        stream.seek(position)
        header = stream.read(8)
        size = int.from_bytes(header[:4], "big")
        kind = header[4:].decode("latin-1")
        header_size = 8
        if size == 1:
            size = int.from_bytes(stream.read(8), "big")
            header_size = 16
        elif size == 0:
            size = end - position
        if size < header_size:
            return
        yield kind, position + header_size, size - header_size
        if kind in _CONTAINER_ATOMS:
            yield from _atoms(stream, position + header_size, position + size)
        position += size


def voice_memo_transcript(path: str) -> str | None:
    """The on-device transcript iOS stores in a Voice Memo's `tsrp` atom, or None."""
    with open(path, "rb") as stream:
        stream.seek(0, 2)
        end = stream.tell()
        for kind, offset, size in _atoms(stream, 0, end):
            if kind != "tsrp":
                continue
            stream.seek(offset)
            try:
                runs = json.loads(stream.read(size))["attributedString"]["runs"]
            except (ValueError, KeyError, TypeError):
                return None
            text = "".join(run for run in runs if isinstance(run, str)).strip()
            return text or None
    return None


def voice_memos_export(inventory: IosBackupInventory, same_bytes: dict[str, tuple[str, str]] | None = None) -> ParsedExport | None:
    db = inventory.voice_memos_db
    if db is None:
        return None
    by_path = {
        item.relative_path: item for item in inventory.audio if item.domain == VOICE_MEMOS_DOMAIN
    }
    connection = sqlite3.connect(f"file:{Path(db.path).as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT ZUNIQUEID, ZPATH, ZDATE, ZDURATION, ZLOCALDURATION, ZENCRYPTEDTITLE, ZCUSTOMLABEL, ZFLAGS "
            "FROM ZCLOUDRECORDING ORDER BY ZDATE"
        ).fetchall()
    finally:
        connection.close()
    records, relationships, warnings = [], [], []
    for unique_id, zpath, zdate, duration, local_duration, title, label, flags in rows:
        if not unique_id:
            continue
        occurred = datetime.fromtimestamp(zdate + CORE_DATA_EPOCH, UTC) if zdate else None
        audio = by_path.get(f"Recordings/{zpath}") if zpath else None
        transcript = None
        if audio is not None:
            try:
                transcript = voice_memo_transcript(audio.path)
            except OSError as error:
                warnings.append(f"voice memo {zpath}: transcript unreadable ({error})")
        seconds = local_duration or duration or None
        records.append(NormalizedRecord(
            source=SOURCE, record_type="voice_memo", source_record_namespace=VOICE_MEMO_NAMESPACE, **_authority(inventory),
            source_record_id=unique_id, title=title or label or zpath, text=transcript,
            created_at=occurred, occurred_at=occurred,
            metadata={
                "path": zpath, "duration_seconds": seconds, "flags": flags,
                "audio_in_backup": audio is not None,
                "transcript_source": "apple_on_device" if transcript else None,
            },
        ))
        if audio is not None:
            relationships.append(NormalizedRelationship(
                source=SOURCE, relationship_type="voice_memo_has_audio", **_authority(inventory),
                source_relationship_id=f"voice_memo:{unique_id}",
                subject_kind="record", subject_source=SOURCE, subject_namespace=VOICE_MEMO_NAMESPACE,
                subject_source_id=unique_id,
                object_kind="asset", object_source=SOURCE, **_asset_ref(audio, same_bytes),
            ))
        else:
            warnings.append(f"voice memo {zpath or unique_id}: audio not in backup (likely iCloud-only)")
    return _export(db, inventory, "voice_memos", _sha256(Path(db.path)), records=tuple(records),
                   relationships=tuple(relationships), parse_warnings=tuple(warnings))


# --- Voicemail --------------------------------------------------------------------------------


def voicemail_transcript(path: str) -> tuple[str | None, float | None]:
    """(text, confidence) from a `<ROWID>.transcript` NSKeyedArchiver plist."""
    with open(path, "rb") as stream:
        archive = plistlib.load(stream)
    objects = archive["$objects"]

    def deref(value):
        return objects[value.data] if isinstance(value, plistlib.UID) else value

    root = deref(archive["$top"]["root"])
    text = deref(root.get("transcriptionString"))
    confidence = deref(root.get("confidence"))
    return (text.strip() or None) if isinstance(text, str) else None, (
        float(confidence) if isinstance(confidence, int | float) else None
    )


def voicemail_export(inventory: IosBackupInventory, same_bytes: dict[str, tuple[str, str]] | None = None) -> ParsedExport | None:
    db = inventory.voicemail_db
    if db is None:
        return None
    audio = {
        PurePosixPath(item.relative_path).stem: item
        for item in sorted(inventory.audio, key=lambda i: i.relative_path.count("/"), reverse=True)
        if item.product == "voicemail"
    }  # top-level Library/Voicemail/<ROWID>.amr wins over the per-account copy
    transcripts = _voicemail_sidecars(inventory)
    connection = sqlite3.connect(f"file:{Path(db.path).as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT ROWID, date, sender, callback_num, receiver, duration, trashed_date, flags, uuid "
            "FROM voicemail ORDER BY date"
        ).fetchall()
    finally:
        connection.close()
    records, relationships, warnings = [], [], []
    parsed_rows = []
    for rowid, date, sender, callback, receiver, duration, trashed, flags, uuid in rows:
        text, confidence = None, None
        sidecar = transcripts.get(str(rowid))
        if sidecar is not None:
            try:
                text, confidence = voicemail_transcript(sidecar)
            except Exception as error:  # noqa: BLE001 -- one bad sidecar must not drop the message
                warnings.append(f"voicemail {rowid}: transcript unreadable ({type(error).__name__})")
        parsed_rows.append((rowid, date, sender, callback, receiver, duration, trashed, flags, uuid, text, confidence))
    # A carrier or account change re-delivers old voicemails under new ROWIDs (same date, caller,
    # and length; identical audio). Keep one per message: the one with a transcript, else the oldest.
    keepers: dict[tuple, tuple] = {}
    duplicates: dict[int, list[int]] = {}
    for row in parsed_rows:
        key = (row[1], row[2], row[5])
        kept = keepers.get(key)
        if kept is None or (row[9] and not kept[9]):
            if kept is not None:
                duplicates.setdefault(row[0], []).extend([kept[0], *duplicates.pop(kept[0], [])])
            keepers[key] = row
        else:
            duplicates.setdefault(kept[0], []).append(row[0])
    for rowid, date, sender, callback, receiver, duration, trashed, flags, uuid, text, confidence in keepers.values():
        record_id = uuid or f"rowid:{rowid}"
        occurred = datetime.fromtimestamp(date, UTC) if date else None
        amr = audio.get(str(rowid))
        records.append(NormalizedRecord(
            source=SOURCE, record_type="voicemail", source_record_namespace=VOICEMAIL_NAMESPACE, **_authority(inventory),
            source_record_id=record_id, title=f"Voicemail from {sender or 'unknown caller'}", text=text,
            created_at=occurred, occurred_at=occurred, actor_identity=sender or None,
            metadata={
                "rowid": rowid, "duration_seconds": duration, "callback_number": callback, "receiver": receiver,
                "trashed": bool(trashed), "flags": flags, "transcript_confidence": confidence,
                "transcript_source": "apple_voicemail" if text else None, "audio_in_backup": amr is not None,
                "duplicate_rowids": sorted(duplicates.get(rowid, [])),
            },
        ))
        if amr is not None:
            relationships.append(NormalizedRelationship(
                source=SOURCE, relationship_type="voicemail_has_audio", **_authority(inventory),
                source_relationship_id=f"voicemail:{record_id}",
                subject_kind="record", subject_source=SOURCE, subject_namespace=VOICEMAIL_NAMESPACE,
                subject_source_id=record_id,
                object_kind="asset", object_source=SOURCE, **_asset_ref(amr, same_bytes),
            ))
    return _export(db, inventory, "voicemail", _sha256(Path(db.path)), records=tuple(records),
                   relationships=tuple(relationships), parse_warnings=tuple(warnings))


def _voicemail_sidecars(inventory: IosBackupInventory) -> dict[str, str]:
    """ROWID -> path of `Library/Voicemail/<ROWID>.transcript`, read straight from Manifest.db."""
    manifest = Path(inventory.root) / "Manifest.db"
    connection = sqlite3.connect(f"file:{manifest.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT fileID, relativePath FROM Files WHERE domain = ? AND flags = 1 "
            "AND relativePath LIKE 'Library/Voicemail/%.transcript'",
            (VOICEMAIL_DOMAIN,),
        ).fetchall()
    finally:
        connection.close()
    root = Path(inventory.root)
    return {PurePosixPath(rel).stem: str(root / fid[:2] / fid) for fid, rel in rows if rel.count("/") == 2}
