"""Inventory an unencrypted iOS device backup (iTunes/Finder/iMazing format: `Manifest.db` plus
content-addressed files under `<first two hex chars>/<fileID>`).

Scope is deliberately audio: every audio file the backup holds, classified by the app that owns
it, plus the two app databases that give those files meaning (Voice Memos' `CloudRecordings.db`,
the Phone app's `voicemail.db`). Nothing is extracted or copied -- an asset's `original_path` is
the backup file itself, which the transcription deriver can open as a plain file.
"""

from __future__ import annotations

import plistlib
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

AUDIO_MIME = {
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".caf": "audio/x-caf",
    ".aac": "audio/aac",
    ".mp3": "audio/mpeg",
    ".amr": "audio/amr",
    ".aif": "audio/aiff",
    ".aiff": "audio/aiff",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".m4b": "audio/mp4",
}

# Audio that is not the owner's own recording: ringtones and purchased audiobooks.
EXCLUDED_DOMAINS = frozenset({"TonesDomain", "AppDomain-com.audible.iphone"})

VOICE_MEMOS_DOMAIN = "AppDomainGroup-group.com.apple.VoiceMemos.shared"
VOICE_MEMOS_DB = "Recordings/CloudRecordings.db"
VOICEMAIL_DOMAIN = "HomeDomain"
VOICEMAIL_DB = "Library/Voicemail/voicemail.db"

# domain -> (product key, human label). Anything else with audio is "other_app_audio".
PRODUCTS: dict[str, tuple[str, str]] = {
    VOICE_MEMOS_DOMAIN: ("voice_memos", "Voice Memos"),
    "AppDomain-com.shure.plus.motiv": ("motiv", "Shure MOTIV Audio"),
    "AppDomain-com.iuuapp.audiomaker": ("audio_editor", "Audio Editor"),
    "AppDomainGroup-group.com.apple.FileProvider.LocalStorage": ("files_app", "Files (On My iPhone)"),
    "AppDomainGroup-group.com.apple.notes": ("notes_audio", "Notes recordings"),
    "AppDomain-com.facebook.Messenger": ("messenger_audio", "Messenger voice messages"),
}


class IosBackupError(ValueError):
    """Raised when a backup cannot be read safely."""


class BackupFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    file_id: str
    domain: str
    relative_path: str
    size_bytes: int = Field(ge=0)
    birth: datetime | None = None
    last_modified: datetime | None = None
    path: str = Field(exclude=True)

    @property
    def filename(self) -> str:
        return PurePosixPath(self.relative_path).name

    @property
    def extension(self) -> str:
        return PurePosixPath(self.relative_path).suffix.lower()

    @property
    def product(self) -> str:
        if self.domain == VOICEMAIL_DOMAIN and self.relative_path.startswith("Library/Voicemail/"):
            return "voicemail"
        return PRODUCTS.get(self.domain, ("other_app_audio", ""))[0]


class IosBackupInventory(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: str
    device_name: str | None
    product_type: str | None
    ios_version: str | None
    backup_date: datetime | None
    unique_device_id: str | None
    manifest_sha256: str
    files_total: int
    audio: tuple[BackupFile, ...]
    voice_memos_db: BackupFile | None
    voicemail_db: BackupFile | None

    def product_counts(self) -> dict[str, tuple[int, int]]:
        counts: Counter[str] = Counter()
        sizes: Counter[str] = Counter()
        for item in self.audio:
            counts[item.product] += 1
            sizes[item.product] += item.size_bytes
        return {key: (counts[key], sizes[key]) for key in sorted(counts)}


def _ts(value: object) -> datetime | None:
    if isinstance(value, int | float) and value > 0:
        return datetime.fromtimestamp(value, UTC)
    return None


def _file_meta(blob: bytes | None) -> dict:
    if not blob:
        return {}
    try:
        archive = plistlib.loads(blob)
        return archive["$objects"][1]
    except Exception:  # noqa: BLE001 -- metadata is best-effort; size falls back to disk
        return {}


def _backup_file(root: Path, file_id: str, domain: str, relative_path: str, blob: bytes | None) -> BackupFile:
    path = root / file_id[:2] / file_id
    meta = _file_meta(blob)
    size = meta.get("Size")
    if not isinstance(size, int):
        size = path.stat().st_size if path.exists() else 0
    return BackupFile(
        file_id=file_id, domain=domain, relative_path=relative_path, size_bytes=size,
        birth=_ts(meta.get("Birth")), last_modified=_ts(meta.get("LastModified")), path=str(path),
    )


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_ios_backup(source: str | Path) -> IosBackupInventory:
    root = Path(source)
    manifest_plist = root / "Manifest.plist"
    manifest_db = root / "Manifest.db"
    if not manifest_plist.is_file() or not manifest_db.is_file():
        raise IosBackupError(f"{root} is not an iOS backup (no Manifest.plist/Manifest.db)")
    with manifest_plist.open("rb") as stream:
        manifest = plistlib.load(stream)
    if manifest.get("IsEncrypted"):
        raise IosBackupError("backup is encrypted; make an unencrypted backup (the password is never handled here)")
    lockdown = manifest.get("Lockdown", {})
    backup_date = manifest.get("Date")
    if isinstance(backup_date, datetime) and backup_date.tzinfo is None:
        backup_date = backup_date.replace(tzinfo=UTC)

    connection = sqlite3.connect(f"file:{manifest_db.as_posix()}?mode=ro", uri=True)
    try:
        files_total = connection.execute("SELECT count(*) FROM Files WHERE flags = 1").fetchone()[0]
        audio: list[BackupFile] = []
        for file_id, domain, relative_path, blob in connection.execute(
            "SELECT fileID, domain, relativePath, file FROM Files WHERE flags = 1 ORDER BY domain, relativePath"
        ):
            if domain in EXCLUDED_DOMAINS or PurePosixPath(relative_path).suffix.lower() not in AUDIO_MIME:
                continue
            audio.append(_backup_file(root, file_id, domain, relative_path, blob))

        def one(domain: str, relative_path: str) -> BackupFile | None:
            row = connection.execute(
                "SELECT fileID, domain, relativePath, file FROM Files WHERE domain = ? AND relativePath = ?",
                (domain, relative_path),
            ).fetchone()
            return _backup_file(root, *row) if row else None

        voice_memos_db = one(VOICE_MEMOS_DOMAIN, VOICE_MEMOS_DB)
        voicemail_db = one(VOICEMAIL_DOMAIN, VOICEMAIL_DB)
    finally:
        connection.close()

    return IosBackupInventory(
        root=str(root),
        device_name=lockdown.get("DeviceName"),
        product_type=lockdown.get("ProductType"),
        ios_version=lockdown.get("ProductVersion"),
        backup_date=backup_date,
        unique_device_id=lockdown.get("UniqueDeviceID"),
        manifest_sha256=_sha256(manifest_db),
        files_total=files_total,
        audio=tuple(audio),
        voice_memos_db=voice_memos_db,
        voicemail_db=voicemail_db,
    )


def find_backup_files(source: str | Path, domain: str, *, path: str | None = None, prefix: str | None = None) -> dict[str, BackupFile]:
    """Files in one domain of a backup, by relative path: one exact `path`, or all under `prefix`."""
    root = Path(source)
    connection = sqlite3.connect(f"file:{(root / 'Manifest.db').as_posix()}?mode=ro", uri=True)
    try:
        if path is not None:
            rows = connection.execute(
                "SELECT fileID, domain, relativePath, file FROM Files WHERE flags = 1 AND domain = ? AND relativePath = ?",
                (domain, path),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT fileID, domain, relativePath, file FROM Files WHERE flags = 1 AND domain = ? "
                "AND substr(relativePath, 1, ?) = ?",
                (domain, len(prefix or ""), prefix or ""),
            ).fetchall()
    finally:
        connection.close()
    return {row[2]: _backup_file(root, *row) for row in rows}


def render_inventory_table(inventory: IosBackupInventory) -> str:
    lines = [
        f"device: {inventory.device_name} ({inventory.product_type}, iOS {inventory.ios_version})",
        f"backup date: {inventory.backup_date}  files: {inventory.files_total:,}",
        (
            f"voice memos db: {'yes' if inventory.voice_memos_db else 'no'}  "
            f"voicemail db: {'yes' if inventory.voicemail_db else 'no'}"
        ),
        "",
        f"{'product':<18} {'files':>6} {'GB':>8}",
    ]
    for product, (count, size) in inventory.product_counts().items():
        lines.append(f"{product:<18} {count:>6} {size / 1e9:>8.2f}")
    return "\n".join(lines)
