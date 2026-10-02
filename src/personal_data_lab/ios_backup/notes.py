"""Apple Notes from the phone (`AppDomainGroup-group.com.apple.notes` `NoteStore.sqlite`).

Three shapes under source `ios_backup`:

* a `note` record per note: title, body text (decoded from the gzipped protobuf in `ZICNOTEDATA`),
  folder, account, dates, checklist items with their done state, and the attachments it holds. A
  note in the Recently Deleted folder is `provider_deleted`; a password-protected note is kept
  with its metadata and no text (its body is encrypted and the password is never handled here);
* a `call_recording` / `note_attachment` record per attachment that carries searchable text: iOS
  stores a call recording's transcript, a scan's recognized text and the like in the attachment
  row (`ZADDITIONALINDEXABLETEXT`). Provider text is record text, never a `media.transcript`;
* an asset per media file under `.../Media/` (audio files are already assets from the audio
  import and are linked, not stored twice), tied to its note by `note_has_attachment` and to its
  transcript record by `note_attachment_has_file`.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import shutil
import sqlite3
import tempfile
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from ..models import NormalizedAsset, NormalizedRecord, NormalizedRelationship, ParsedExport
from .inventory import BackupFile, IosBackupInventory, find_backup_files

SOURCE = "ios_backup"
PARSER_VERSION = "ios-notes-v1"
NOTES_DOMAIN = "AppDomainGroup-group.com.apple.notes"
NOTE_STORE = "NoteStore.sqlite"
NOTE_NAMESPACE = "ios_backup/notes/note"
ATTACHMENT_NAMESPACE = "ios_backup/notes/attachment"
MEDIA_ASSET_NAMESPACE = "ios_backup/notes_media"
AUDIO_ASSET_NAMESPACE = "ios_backup/notes_audio"
APPLE_EPOCH = 978307200
_ENT_NOTE, _ENT_ACCOUNT, _ENT_ATTACHMENT, _ENT_MEDIA, _ENT_FOLDER = 12, 14, 5, 11, 15
_CHECKLIST = 103
_PLACEHOLDER = "￼"
_AUDIO_UTI = re.compile(r"audio|m4a|mpeg-4-audio|recording", re.IGNORECASE)


# --- protobuf (just enough for the note body) -----------------------------------------------------


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    shift = value = 0
    while True:
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def _fields(buf: bytes) -> list[tuple[int, int, object]]:
    """(field number, wire type, value) for each field; length-delimited values are bytes."""
    out, pos = [], 0
    while pos < len(buf):
        key, pos = _varint(buf, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
        elif wire == 2:
            size, pos = _varint(buf, pos)
            value, pos = buf[pos : pos + size], pos + size
        elif wire == 1:
            value, pos = buf[pos : pos + 8], pos + 8
        elif wire == 5:
            value, pos = buf[pos : pos + 4], pos + 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        out.append((number, wire, value))
    return out


def _first(fields: list[tuple[int, int, object]], number: int):
    return next((value for n, _w, value in fields if n == number), None)


@dataclass
class NoteBody:
    text: str
    checklist: list[dict] = field(default_factory=list)
    attachment_ids: list[str] = field(default_factory=list)


def decode_note_body(blob: bytes) -> NoteBody | None:
    """Text, checklist items and inline attachment ids from a `ZICNOTEDATA.ZDATA` blob
    (gzip or zlib of NoteStoreProto: document = 2, note = 3, note_text = 2, attribute_run = 5)."""
    try:
        root = _fields(zlib.decompress(blob, 47))  # 47: gzip or zlib framing
        document = _first(root, 2)
        note = _first(_fields(document), 3) if document else None
        if note is None:
            return None
        note_fields = _fields(note)
        raw = _first(note_fields, 2)
        text = raw.decode("utf-8") if isinstance(raw, bytes) else ""
        body = NoteBody(text=text)
        runs: list[tuple[int, int, int | None, bool]] = []  # start, length, style type, done
        offset = 0
        for number, _wire, value in note_fields:
            if number != 5:
                continue
            run = _fields(value)
            length = _first(run, 1) or 0
            style = _first(run, 2)
            attachment = _first(run, 12)
            if attachment:
                identifier = _first(_fields(attachment), 1)
                if isinstance(identifier, bytes):
                    body.attachment_ids.append(identifier.decode("utf-8", "replace"))
            style_type, done = None, False
            if style:
                style_fields = _fields(style)
                style_type = _first(style_fields, 1)
                todo = _first(style_fields, 5)
                done = bool(_first(_fields(todo), 2)) if todo else False
            runs.append((offset, length, style_type, done))
            offset += length
        # A checklist item is a paragraph whose first run is styled as a checklist; run lengths are
        # UTF-16 code units, so paragraphs are located in the UTF-16 form of the text.
        units = text.encode("utf-16-le")
        position = 0
        for line in text.split("\n"):
            width = len(line.encode("utf-16-le")) // 2
            run = next((r for r in runs if r[0] <= position < r[0] + r[1]), None)
            if run is not None and run[2] == _CHECKLIST:
                item = units[2 * position : 2 * (position + width)].decode("utf-16-le", "replace")
                item = item.replace(_PLACEHOLDER, "").strip()
                if item:
                    body.checklist.append({"text": item, "done": run[3]})
            position += width + 1
        return body
    except (ValueError, IndexError, OSError, UnicodeDecodeError, zlib.error):
        return None


# --- reading the store -----------------------------------------------------------------------------


@dataclass
class NoteAttachment:
    identifier: str
    uti: str | None
    title: str | None
    summary: str | None
    text: str | None
    media_identifier: str | None
    filename: str | None
    created: datetime | None


@dataclass
class DeviceNote:
    identifier: str
    title: str | None
    snippet: str | None
    folder: str | None
    account: str | None
    created: datetime | None
    modified: datetime | None
    deleted: bool
    locked: bool
    body: NoteBody | None
    attachments: list[NoteAttachment] = field(default_factory=list)


def _when(value: float | None) -> datetime | None:
    return datetime.fromtimestamp(value + APPLE_EPOCH, UTC) if value else None


def read_note_store(inventory: IosBackupInventory) -> list[DeviceNote]:
    files = {
        suffix: find_backup_files(inventory.root, NOTES_DOMAIN, path=NOTE_STORE + suffix)
        for suffix in ("", "-wal", "-shm")
    }
    if not files[""]:
        return []
    with tempfile.TemporaryDirectory(prefix="pdl-notes-") as tmp:
        for suffix, found in files.items():
            if found:
                shutil.copyfile(next(iter(found.values())).path, Path(tmp) / f"n.db{suffix}")
        db = sqlite3.connect(Path(tmp) / "n.db")
        try:
            return _read(db)
        finally:
            db.close()


def _read(db: sqlite3.Connection) -> list[DeviceNote]:
    folders = {pk: (title, kind) for pk, title, kind in db.execute(
        f"SELECT Z_PK, ZTITLE2, ZFOLDERTYPE FROM ZICCLOUDSYNCINGOBJECT WHERE Z_ENT = {_ENT_FOLDER}")}
    accounts = {pk: name for pk, name in db.execute(
        f"SELECT Z_PK, ZNAME FROM ZICCLOUDSYNCINGOBJECT WHERE Z_ENT = {_ENT_ACCOUNT}")}
    media = {pk: (ident, filename) for pk, ident, filename in db.execute(
        f"SELECT Z_PK, ZIDENTIFIER, ZFILENAME FROM ZICCLOUDSYNCINGOBJECT WHERE Z_ENT = {_ENT_MEDIA}")}
    blobs = {note: data for note, data in db.execute("SELECT ZNOTE, ZDATA FROM ZICNOTEDATA")}
    attachments: dict[int, list[NoteAttachment]] = {}
    for pk, note, media_pk, uti, ident, title, summary, extra, created in db.execute(
        "SELECT Z_PK, ZNOTE, ZMEDIA, ZTYPEUTI, ZIDENTIFIER, ZTITLE, ZSUMMARY, ZADDITIONALINDEXABLETEXT, "
        f"ZCREATIONDATE FROM ZICCLOUDSYNCINGOBJECT WHERE Z_ENT = {_ENT_ATTACHMENT} AND ZNOTE IS NOT NULL"
    ):
        media_ident, filename = media.get(media_pk, (None, None))
        attachments.setdefault(note, []).append(NoteAttachment(
            identifier=ident or f"attachment-{pk}", uti=uti, title=title, summary=summary,
            text=(extra or "").strip() or None, media_identifier=media_ident, filename=filename,
            created=_when(created),
        ))
    notes = []
    for pk, ident, title, snippet, folder_pk, account_pk, created, modified, locked, marked in db.execute(
        "SELECT Z_PK, ZIDENTIFIER, ZTITLE1, ZSNIPPET, ZFOLDER, ZACCOUNT7, ZCREATIONDATE3, "
        "ZMODIFICATIONDATE1, ZISPASSWORDPROTECTED, ZMARKEDFORDELETION "
        f"FROM ZICCLOUDSYNCINGOBJECT WHERE Z_ENT = {_ENT_NOTE} ORDER BY Z_PK"
    ):
        folder, kind = folders.get(folder_pk, (None, None))
        notes.append(DeviceNote(
            identifier=ident or f"note-{pk}", title=title, snippet=snippet, folder=folder,
            account=accounts.get(account_pk), created=_when(created), modified=_when(modified),
            deleted=bool(marked) or kind == 1, locked=bool(locked),
            body=None if locked or pk not in blobs or not blobs[pk] else decode_note_body(blobs[pk]),
            attachments=attachments.get(pk, []),
        ))
    return notes


# --- ParsedExport ----------------------------------------------------------------------------------


def _is_audio(attachment: NoteAttachment) -> bool:
    return bool(attachment.uti and _AUDIO_UTI.search(attachment.uti))


def media_files(inventory: IosBackupInventory) -> dict[str, BackupFile]:
    """Notes media files by media identifier (the folder name under `.../Media/`)."""
    out: dict[str, BackupFile] = {}
    for relative, item in find_backup_files(inventory.root, NOTES_DOMAIN, prefix="Accounts/").items():
        parts = PurePosixPath(relative).parts
        if "Media" in parts and parts.index("Media") + 2 < len(parts):
            out.setdefault(parts[parts.index("Media") + 1], item)
    return out


def asset_ref(item: BackupFile, existing: dict[str, tuple[str, str]]) -> tuple[str, str]:
    source_id = f"{item.domain}/{item.relative_path}"
    return existing.get(source_id) or (MEDIA_ASSET_NAMESPACE, source_id)


def _relationship(kind: str, subject: tuple[str, str, str, str], obj: tuple[str, str, str, str],
                  authority_at: datetime | None) -> NormalizedRelationship:
    identity = hashlib.sha256("|".join([kind, *subject, *obj]).encode()).hexdigest()
    return NormalizedRelationship(
        source=SOURCE, relationship_type=kind, source_relationship_namespace=f"ios_backup/notes/{kind}",
        source_relationship_id=f"relationship:{identity}", subject_kind=subject[0],  # type: ignore[arg-type]
        subject_source=subject[1], subject_namespace=subject[2], subject_source_id=subject[3],
        object_kind=obj[0], object_source=obj[1], object_namespace=obj[2],  # type: ignore[arg-type]
        object_source_id=obj[3], authority_at=authority_at,
        authority_kind="exported_at" if authority_at else "unknown",
    )


def notes_export(
    notes: list[DeviceNote],
    inventory: IosBackupInventory,
    files: dict[str, BackupFile],
    existing_assets: dict[str, tuple[str, str]],
    store: BackupFile,
    sha256: str,
) -> ParsedExport:
    """Records and relationships for every note; the media assets themselves come from
    `media_asset_export` (one per file)."""
    stamp = inventory.backup_date
    authority = {"authority_at": stamp, "authority_kind": "exported_at" if stamp else "unknown"}
    records, relationships = [], []
    for note in notes:
        ref = ("record", SOURCE, NOTE_NAMESPACE, note.identifier)
        meta = {k: v for k, v in {
            "folder": note.folder, "account": note.account, "snippet": note.snippet,
            "locked": note.locked or None, "deleted": note.deleted or None,
            "body_missing": True if note.body is None and not note.locked else None,
            "checklist": note.body.checklist if note.body and note.body.checklist else None,
            "attachments": [{k: v for k, v in {
                "identifier": a.identifier, "type": a.uti, "title": a.title, "summary": a.summary,
                "filename": a.filename, "has_text": bool(a.text) or None,
            }.items() if v is not None} for a in note.attachments] or None,
        }.items() if v is not None}
        text_body = note.body.text.replace(_PLACEHOLDER, "").strip() if note.body else ""
        records.append(NormalizedRecord(
            source=SOURCE, record_type="note", source_record_namespace=NOTE_NAMESPACE,
            source_record_id=note.identifier, title=note.title, text=text_body or None,
            created_at=note.created, updated_at=note.modified, occurred_at=note.created,
            metadata=meta, lifecycle_state="provider_deleted" if note.deleted else "active", **authority,
        ))
        for attachment in note.attachments:
            item = files.get(attachment.media_identifier or "")
            target = ("asset", SOURCE, *asset_ref(item, existing_assets)) if item else None
            if target:
                relationships.append(_relationship("note_has_attachment", ref, target, stamp))
            if attachment.text:
                kind = "call_recording" if _is_audio(attachment) else "note_attachment"
                record_id = attachment.identifier
                records.append(NormalizedRecord(
                    source=SOURCE, record_type=kind, source_record_namespace=ATTACHMENT_NAMESPACE,
                    source_record_id=record_id, title=attachment.title or note.title,
                    text=attachment.text, occurred_at=attachment.created or note.created,
                    created_at=attachment.created or note.created,
                    metadata={k: v for k, v in {
                        "note": note.identifier, "type": attachment.uti, "filename": attachment.filename,
                        "transcript_source": "apple_notes" if kind == "call_recording" else None,
                    }.items() if v is not None},
                    lifecycle_state="provider_deleted" if note.deleted else "active", **authority,
                ))
                if target:
                    relationships.append(_relationship(
                        "note_attachment_has_file", ("record", SOURCE, ATTACHMENT_NAMESPACE, record_id),
                        target, stamp))
    return ParsedExport(
        source=SOURCE, parser_version=PARSER_VERSION, category="notes", filename=NOTE_STORE,
        original_path=store.path, sha256=sha256, size_bytes=store.size_bytes, exported_at=stamp,
        export_id=f"{inventory.unique_device_id}:{inventory.manifest_sha256[:16]}",
        snapshot_kind="partial", snapshot_scope="device_backup_notes",
        records=tuple(records), relationships=tuple(relationships),
    )


def media_asset_export(item: BackupFile, inventory: IosBackupInventory, sha256: str) -> ParsedExport:
    mime = mimetypes.guess_type(item.filename)[0]
    asset = NormalizedAsset(
        source=SOURCE, asset_namespace=MEDIA_ASSET_NAMESPACE, source_asset_id=f"{item.domain}/{item.relative_path}",
        sha256=sha256, mime_type=mime, filename=item.filename, original_path=item.path,
        size_bytes=item.size_bytes, created_at=item.birth, captured_at=item.birth or item.last_modified,
        modified_at=item.last_modified,
        metadata={"domain": item.domain, "relative_path": item.relative_path, "product": "notes_media",
                  "backup_file_id": item.file_id},
        authority_at=inventory.backup_date,
        authority_kind="exported_at" if inventory.backup_date else "unknown",
    )
    return ParsedExport(
        source=SOURCE, parser_version=PARSER_VERSION, category="notes_media", filename=item.filename,
        original_path=item.path, sha256=sha256, size_bytes=item.size_bytes, exported_at=inventory.backup_date,
        export_id=f"{inventory.unique_device_id}:{inventory.manifest_sha256[:16]}",
        snapshot_kind="partial", snapshot_scope="device_backup_notes", assets=(asset,),
    )
