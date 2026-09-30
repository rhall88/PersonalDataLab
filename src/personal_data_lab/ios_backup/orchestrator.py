"""Import every audio file (and the Voice Memos / voicemail databases) from an iOS backup."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from sqlalchemy.engine import Engine

from ..database import import_parsed_exports
from ..models import ParsedExport
from .adapters import asset_id, asset_namespace, audio_export, voice_memos_export, voicemail_export
from .inventory import BackupFile, inspect_ios_backup

ProgressCallback = Callable[[str, int, int], None]


class IosBackupImportSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    device: str
    audio_files: int
    audio_imported: int
    audio_short_circuited: int
    audio_failed: int
    failed_paths: tuple[str, ...]
    per_product: dict[str, int]
    voice_memo_records: int
    voice_memo_transcripts: int
    voicemail_records: int
    voicemail_transcripts: int
    parse_warnings: tuple[str, ...]

    @property
    def exit_code(self) -> int:
        return 1 if self.audio_failed else 0


def _import_one(engine: Engine, parsed: ParsedExport) -> bool:
    (result,) = import_parsed_exports(engine, (parsed,))
    return result.short_circuited


def import_ios_backup_all(
    engine: Engine, source: str | Path, *, on_progress: ProgressCallback | None = None
) -> IosBackupImportSummary:
    """Assets first, then the two databases, so every relationship's object already exists."""
    inventory = inspect_ios_backup(source)
    imported = short_circuited = 0
    failed: list[BackupFile] = []
    per_product: Counter[str] = Counter()
    first_by_sha: dict[str, tuple[str, str]] = {}
    same_bytes: dict[str, tuple[str, str]] = {}
    total = len(inventory.audio)
    for index, item in enumerate(inventory.audio, 1):
        try:
            parsed = audio_export(item, inventory)
            owner = first_by_sha.setdefault(parsed.sha256, (asset_namespace(item.product), asset_id(item)))
            same_bytes[asset_id(item)] = owner
            short_circuited += _import_one(engine, parsed)
            imported += 1
            per_product[item.product] += 1
        except Exception:  # noqa: BLE001 -- one unreadable file must not stop the backup
            failed.append(item)
        if on_progress is not None:
            on_progress(item.product, index, total)

    warnings: list[str] = []
    memo_records = memo_transcripts = vm_records = vm_transcripts = 0
    memos = voice_memos_export(inventory, same_bytes)
    if memos is not None:
        _import_one(engine, memos)
        memo_records = len(memos.records)
        memo_transcripts = sum(1 for record in memos.records if record.text)
        warnings.extend(memos.parse_warnings)
    voicemail = voicemail_export(inventory, same_bytes)
    if voicemail is not None:
        _import_one(engine, voicemail)
        vm_records = len(voicemail.records)
        vm_transcripts = sum(1 for record in voicemail.records if record.text)
        warnings.extend(voicemail.parse_warnings)

    return IosBackupImportSummary(
        device=f"{inventory.device_name} ({inventory.product_type}, iOS {inventory.ios_version})",
        audio_files=total,
        audio_imported=imported,
        audio_short_circuited=short_circuited,
        audio_failed=len(failed),
        failed_paths=tuple(f"{item.domain}/{item.relative_path}" for item in failed),
        per_product=dict(sorted(per_product.items())),
        voice_memo_records=memo_records,
        voice_memo_transcripts=memo_transcripts,
        voicemail_records=vm_records,
        voicemail_transcripts=vm_transcripts,
        parse_warnings=tuple(warnings),
    )
