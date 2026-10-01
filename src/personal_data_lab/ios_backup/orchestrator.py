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
from .inventory import BackupFile, _sha256, inspect_ios_backup

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


# --- Messages -----------------------------------------------------------------------------------


class IosMessagesImportSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    stores: dict[str, int]  # store label -> messages read (all kinds)
    device_messages: int  # GUID-unique, all stores
    matched_to_csv: int
    imported: dict[str, int]  # kind -> count
    caption_twins: int
    skipped: dict[str, int]
    per_store: dict[str, int]  # store label -> imported items
    attachments_linked: int
    attachments_local: int
    chunks: int
    chunks_short_circuited: int
    identity_links_mirrored: int


def _store_sidecars(source: Path) -> tuple[Path, dict[str, Path]]:
    from .inventory import find_backup_files
    from .messages import SMS_DB, SMS_DOMAIN, MessageStoreError

    files = {}
    for suffix in ("", "-wal", "-shm"):
        found = find_backup_files(source, SMS_DOMAIN, path=SMS_DB + suffix)
        if found:
            files[suffix] = Path(next(iter(found.values())).path)
    if "" not in files:
        raise MessageStoreError(f"{source} has no {SMS_DB}")
    return files[""], {k: v for k, v in files.items() if k}


def mirror_identity_links(engine: Engine) -> int:
    """Link each device handle to the person its CSV twin identity is already linked to (the same
    phone number or email, confirmed on the CSV source). Only handles the device source actually
    has; never re-links a handle that is linked elsewhere."""
    from sqlalchemy import text

    from ..identity import merge_identity
    from .messages import CSV_SOURCE
    from .messages import SOURCE as MESSAGES_SOURCE

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT il.person_id, il.identity_value FROM identity_links il "
                "WHERE il.source = :csv AND il.lifecycle_state = 'active' "
                "AND EXISTS (SELECT 1 FROM events e WHERE e.source = :device AND e.actor_identity = il.identity_value) "
                "AND NOT EXISTS (SELECT 1 FROM identity_links d WHERE d.source = :device "
                "AND d.identity_value = il.identity_value AND d.lifecycle_state = 'active') "
                "ORDER BY il.identity_value"
            ),
            {"csv": CSV_SOURCE, "device": MESSAGES_SOURCE},
        ).all()
    for person_id, value in rows:
        merge_identity(
            engine, person_id, MESSAGES_SOURCE, value, tier="computed",
            note=f"same handle as the confirmed {CSV_SOURCE} identity",
            evidence={"via": "mirrored_from", "source": CSV_SOURCE, "identity_value": value},
        )
    return len(rows)


def import_ios_messages(
    engine: Engine,
    backup: str | Path,
    *,
    snapshots: tuple[str | Path, ...] = (),
    on_progress: ProgressCallback | None = None,
) -> IosMessagesImportSummary:
    """Import what the CSV source lacks from the backup's sms.db (then older copies of the same
    database, for messages since deleted), link every matched CSV message to its device copy, and
    mirror the CSV source's confirmed identity links onto the device handles."""
    from datetime import UTC, datetime

    from sqlalchemy import text

    from .inventory import find_backup_files
    from .messages import (
        ATTACHMENT_DOMAIN,
        BackupAttachment,
        iter_message_exports,
        load_csv_index,
        plan_import,
        read_message_store,
    )

    inventory = inspect_ios_backup(backup)
    database, sidecars = _store_sidecars(Path(backup))
    stores = [read_message_store(database, "device", sidecars=sidecars, exported_at=inventory.backup_date)]
    for snapshot in snapshots:
        path = Path(snapshot)
        taken = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        stores.append(read_message_store(path, f"snapshot-{taken:%Y-%m-%d}", exported_at=taken))

    with engine.connect() as connection:
        csv = load_csv_index(connection)
        existing = {
            source_asset_id: (namespace, source_asset_id)
            for namespace, source_asset_id in connection.execute(
                text(
                    "SELECT asset_namespace, source_asset_id FROM assets WHERE source = 'ios_backup' "
                    "AND source_asset_id LIKE :prefix"
                ),
                {"prefix": f"{ATTACHMENT_DOMAIN}/Library/SMS/%"},
            )
        }
    plan = plan_import(stores, csv)

    on_device = find_backup_files(backup, ATTACHMENT_DOMAIN, prefix="Library/SMS/Attachments/")
    backup_files: dict[str, BackupAttachment] = {}
    linked = local = 0
    for message in plan.imported:
        for attachment in message.attachments:
            linked += 1
            item = on_device.get(attachment.relative_path or "")
            if item is None or not Path(item.path).is_file():
                continue
            local += 1
            owner = existing.get(f"{ATTACHMENT_DOMAIN}/{item.relative_path}")
            backup_files[item.relative_path] = BackupAttachment(
                path=item.path,
                size_bytes=item.size_bytes,
                sha256=None if owner else _sha256(Path(item.path)),
                existing=("ios_backup", *owner) if owner else None,
            )

    chunks = short_circuited = 0
    per_store: Counter[str] = Counter(m.store for m in plan.imported)
    export_id = f"{inventory.unique_device_id}:{inventory.manifest_sha256[:16]}"
    for parsed in iter_message_exports(plan, export_id=export_id, backup_files=backup_files):
        short_circuited += _import_one(engine, parsed)
        chunks += 1
        if on_progress is not None:
            on_progress(parsed.category or "", chunks, 0)

    mirrored = mirror_identity_links(engine)
    return IosMessagesImportSummary(
        stores={store.label: len(store.messages) for store in stores},
        device_messages=len(plan.messages),
        matched_to_csv=len(plan.twins),
        imported=dict(sorted(Counter(m.kind for m in plan.imported).items())),
        caption_twins=len(plan.caption_twins),
        skipped=dict(sorted(plan.skipped.items())),
        per_store=dict(sorted(per_store.items())),
        attachments_linked=linked,
        attachments_local=local,
        chunks=chunks,
        chunks_short_circuited=short_circuited,
        identity_links_mirrored=mirrored,
    )
