from __future__ import annotations

from collections.abc import Callable
from time import perf_counter

from pydantic import BaseModel, ConfigDict, computed_field
from sqlalchemy.engine import Engine

from ..database import import_parsed_exports
from ..models import ImportResult, ParsedExport
from .inventory import AnthropicArchive, AnthropicInventory, inspect_anthropic
from .parser import iter_archive_exports, parse_manifest_export


class AnthropicFileCoverage(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    category: str
    members_discovered: int = 0
    members_processed: int = 0
    members_failed: int = 0
    chunks: int = 0
    short_circuited_chunks: int = 0
    conversations: int = 0
    events: int = 0
    records: int = 0
    assets: int = 0
    available_assets: int = 0
    unavailable_assets: int = 0
    relationships: int = 0
    parent_relationships: int = 0
    attachment_relationships: int = 0
    conflicts: int = 0
    parse_warnings: int = 0
    failures: int = 0
    elapsed_seconds: float = 0.0


class AnthropicImportSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_files_total: int
    declared_archives: int
    undeclared_archives: int
    physical_source_bytes: int
    archive_members_total: int
    structured_json_members: int
    binary_asset_members: int
    files_processed: int
    files_short_circuited: int
    files_failed: int
    members_processed: int
    members_failed: int
    chunks: int
    conversations: int
    events: int
    records: int
    assets: int
    available_assets: int
    unavailable_assets: int
    relationships: int
    parent_relationships: int
    attachment_relationships: int
    conflicts: int
    parse_warnings: int
    coverage: dict[str, AnthropicFileCoverage]

    @computed_field
    @property
    def unknown_files(self) -> int:
        return self.undeclared_archives

    @computed_field
    @property
    def complete_accounting(self) -> bool:
        return bool(
            self.source_files_total == self.files_processed + self.files_failed + self.unknown_files
            and self.archive_members_total == self.members_processed + self.members_failed
            and self.assets == self.available_assets + self.unavailable_assets
        )

    @computed_field
    @property
    def production_ready(self) -> bool:
        return bool(
            self.complete_accounting
            and not self.unknown_files
            and not self.files_failed
            and not self.members_failed
            and not self.conflicts
            and not self.parse_warnings
        )

    @computed_field
    @property
    def exit_code(self) -> int:
        if self.files_failed or self.members_failed:
            return 4
        if self.unknown_files:
            return 5
        if self.conflicts:
            return 2
        if self.parse_warnings or not self.complete_accounting:
            return 3
        return 0


StartedCallback = Callable[[str, str, int], None]
ImportedCallback = Callable[[str, ParsedExport, ImportResult], None]


def _conflicts(result: ImportResult) -> int:
    return (
        result.conflicted_conversations
        + result.conflicted_events
        + result.conflicted_records
        + result.conflicted_assets
        + result.conflicted_relationships
    )


def _counts(parsed: ParsedExport) -> dict[str, int]:
    available_assets = sum(
        bool(item.metadata.get("anthropic", {}).get("available_in_export"))
        for item in parsed.assets
    )
    return {
        "conversations": parsed.conversation_count,
        "events": parsed.event_count,
        "records": parsed.record_count,
        "assets": parsed.asset_count,
        "available_assets": available_assets,
        "unavailable_assets": parsed.asset_count - available_assets,
        "relationships": parsed.relationship_count,
        "parent_relationships": sum(
            item.relationship_type == "message_has_parent" for item in parsed.relationships
        ),
        "attachment_relationships": sum(
            item.relationship_type in {"message_has_attachment", "message_has_file"}
            for item in parsed.relationships
        ),
    }


def _import_one(
    engine: Engine,
    source_id: str,
    parsed: ParsedExport,
    *,
    on_member_imported: ImportedCallback | None,
    mutable: dict[str, int],
) -> None:
    result = import_parsed_exports(engine, (parsed,))[0]
    mutable["chunks"] += 1
    mutable["short_circuited_chunks"] += int(result.short_circuited)
    mutable["conflicts"] += _conflicts(result)
    mutable["parse_warnings"] += len(parsed.parse_warnings)
    for name, value in _counts(parsed).items():
        mutable[name] += value
    if on_member_imported is not None:
        on_member_imported(source_id, parsed, result)


def _blank_counts() -> dict[str, int]:
    return {
        "members_processed": 0,
        "members_failed": 0,
        "chunks": 0,
        "short_circuited_chunks": 0,
        "conversations": 0,
        "events": 0,
        "records": 0,
        "assets": 0,
        "available_assets": 0,
        "unavailable_assets": 0,
        "relationships": 0,
        "parent_relationships": 0,
        "attachment_relationships": 0,
        "conflicts": 0,
        "parse_warnings": 0,
    }


def _archive_coverage(
    engine: Engine,
    inventory: AnthropicInventory,
    archive: AnthropicArchive,
    *,
    seen_conversations: set[tuple[str, str]],
    seen_events: set[tuple[str, str]],
    on_member_start: StartedCallback | None,
    on_member_imported: ImportedCallback | None,
) -> AnthropicFileCoverage:
    started = perf_counter()
    mutable = _blank_counts()
    failures = 0
    if on_member_start is not None:
        on_member_start(archive.archive_id, archive.category, archive.size_bytes)
    try:
        for member, parsed in iter_archive_exports(
            inventory,
            archive,
            seen_conversations=seen_conversations,
            seen_events=seen_events,
        ):
            _import_one(
                engine,
                member.member_id,
                parsed,
                on_member_imported=on_member_imported,
                mutable=mutable,
            )
        if not mutable["chunks"]:
            raise ValueError("accounted archive produced no parsed exports")
        mutable["members_processed"] = archive.members_total
    except Exception:  # noqa: BLE001 - report only opaque archive/member identities
        failures = 1
        mutable["members_processed"] = 0
        mutable["members_failed"] = archive.members_total
    return AnthropicFileCoverage(
        source_id=archive.archive_id,
        category=archive.category,
        members_discovered=archive.members_total,
        failures=failures,
        elapsed_seconds=perf_counter() - started,
        **mutable,
    )


def import_anthropic_all(
    engine: Engine,
    manifest_path: str,
    *,
    on_member_start: StartedCallback | None = None,
    on_member_imported: ImportedCallback | None = None,
) -> AnthropicImportSummary:
    """Inventory and import an official Anthropic export in restart-safe units."""

    inventory = inspect_anthropic(manifest_path)
    coverage: dict[str, AnthropicFileCoverage] = {}
    seen_conversations: set[tuple[str, str]] = set()
    seen_events: set[tuple[str, str]] = set()

    manifest_started = perf_counter()
    manifest_counts = _blank_counts()
    manifest_failures = 0
    if on_member_start is not None:
        on_member_start(
            inventory.manifest_id,
            "manifest",
            inventory.manifest_size_bytes,
        )
    try:
        parsed_manifest = parse_manifest_export(inventory)
        _import_one(
            engine,
            inventory.manifest_id,
            parsed_manifest,
            on_member_imported=on_member_imported,
            mutable=manifest_counts,
        )
    except Exception:  # noqa: BLE001 - report only opaque source identities
        manifest_failures = 1
    coverage[inventory.manifest_id] = AnthropicFileCoverage(
        source_id=inventory.manifest_id,
        category="manifest",
        failures=manifest_failures,
        elapsed_seconds=perf_counter() - manifest_started,
        **manifest_counts,
    )

    for archive in inventory.archives:
        item = _archive_coverage(
            engine,
            inventory,
            archive,
            seen_conversations=seen_conversations,
            seen_events=seen_events,
            on_member_start=on_member_start,
            on_member_imported=on_member_imported,
        )
        coverage[archive.archive_id] = item

    files_processed = sum(not item.failures for item in coverage.values())
    files_failed = sum(bool(item.failures) for item in coverage.values())
    files_short_circuited = sum(
        bool(item.chunks and item.short_circuited_chunks == item.chunks)
        for item in coverage.values()
    )
    names = (
        "members_processed",
        "members_failed",
        "chunks",
        "conversations",
        "events",
        "records",
        "assets",
        "available_assets",
        "unavailable_assets",
        "relationships",
        "parent_relationships",
        "attachment_relationships",
        "conflicts",
        "parse_warnings",
    )
    totals = {name: sum(int(getattr(item, name)) for item in coverage.values()) for name in names}
    return AnthropicImportSummary(
        source_files_total=inventory.source_files_total,
        declared_archives=inventory.declared_archives,
        undeclared_archives=inventory.undeclared_archives,
        physical_source_bytes=inventory.physical_source_bytes,
        archive_members_total=inventory.archive_members_total,
        structured_json_members=inventory.structured_json_members,
        binary_asset_members=inventory.binary_asset_members,
        files_processed=files_processed,
        files_short_circuited=files_short_circuited,
        files_failed=files_failed,
        coverage=coverage,
        **totals,
    )
