from __future__ import annotations

from collections.abc import Callable
from time import perf_counter

from pydantic import BaseModel, ConfigDict, computed_field
from sqlalchemy.engine import Engine

from ..database import import_parsed_exports
from ..models import ImportResult, ParsedExport
from .inventory import (
    OpenAIExportReader,
    OpenAIPhysicalMember,
    _inspect,
)
from .parser import (
    MESSAGE_NAMESPACE,
    OpenAIAdapterError,
    iter_conversation_member_exports,
    load_asset_catalog,
    parse_asset_member,
    parse_structured_member,
)


class OpenAIFileCoverage(BaseModel):
    model_config = ConfigDict(frozen=True)

    member_id: str
    accounting_category: str
    chunks: int = 0
    short_circuited_chunks: int = 0
    conversations: int = 0
    messages: int = 0
    records: int = 0
    assets: int = 0
    relationships: int = 0
    parent_relationships: int = 0
    asset_references: int = 0
    resolved_asset_references: int = 0
    unavailable_asset_references: int = 0
    conflicts: int = 0
    parse_warnings: int = 0
    failures: int = 0
    elapsed_seconds: float = 0.0


class OpenAIImportSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    physical_members_total: int
    logical_files_total: int
    physical_source_bytes: int
    logical_source_bytes: int
    conversation_shards: int
    structured_json_files: int
    exported_asset_files: int
    rendered_html_files: int
    unknown_files: int
    files_processed: int
    files_short_circuited: int
    files_failed: int
    chunks: int
    conversations: int
    messages: int
    records: int
    exported_assets: int
    unavailable_assets: int
    relationships: int
    parent_relationships: int
    asset_references: int
    resolved_asset_references: int
    unavailable_asset_references: int
    conflicts: int
    parse_warnings: int
    coverage: dict[str, OpenAIFileCoverage]

    @computed_field
    @property
    def complete_accounting(self) -> bool:
        return bool(
            self.physical_members_total
            == self.files_processed + self.files_failed + self.unknown_files
            and self.asset_references
            == self.resolved_asset_references + self.unavailable_asset_references
            and self.exported_assets == self.exported_asset_files + self.rendered_html_files
        )

    @computed_field
    @property
    def production_ready(self) -> bool:
        return bool(
            self.complete_accounting
            and not self.unknown_files
            and not self.files_failed
            and not self.conflicts
            and not self.parse_warnings
        )

    @computed_field
    @property
    def exit_code(self) -> int:
        if self.files_failed:
            return 4
        if self.unknown_files:
            return 5
        if self.conflicts:
            return 2
        if self.parse_warnings or not self.complete_accounting:
            return 3
        return 0


MemberCallback = Callable[[OpenAIPhysicalMember], None]
ImportedCallback = Callable[[OpenAIPhysicalMember, ParsedExport, ImportResult], None]


def _conflicts(result: ImportResult) -> int:
    return (
        result.conflicted_conversations
        + result.conflicted_events
        + result.conflicted_records
        + result.conflicted_assets
        + result.conflicted_relationships
    )


def _parsed_counts(parsed: ParsedExport) -> dict[str, object]:
    events = [event for conversation in parsed.conversations for event in conversation.events]
    asset_relationships = [
        item for item in parsed.relationships if item.relationship_type == "message_has_asset"
    ]
    unavailable_ids = {
        item.object_source_id
        for item in asset_relationships
        if not item.metadata.get("openai", {}).get("available_in_export", False)
    }
    return {
        "conversations": len(parsed.conversations),
        "messages": sum(item.source_record_namespace == MESSAGE_NAMESPACE for item in events),
        "records": len(parsed.records),
        "assets": len(parsed.assets),
        "relationships": len(parsed.relationships),
        "parent_relationships": sum(
            item.relationship_type == "message_has_parent" for item in parsed.relationships
        ),
        "asset_references": len(asset_relationships),
        "resolved_asset_references": sum(
            bool(item.metadata.get("openai", {}).get("available_in_export"))
            for item in asset_relationships
        ),
        "unavailable_asset_references": sum(
            not item.metadata.get("openai", {}).get("available_in_export", False)
            for item in asset_relationships
        ),
        "unavailable_ids": unavailable_ids,
    }


def _import_member(
    engine: Engine,
    member: OpenAIPhysicalMember,
    parsed_exports,
    *,
    on_member_imported: ImportedCallback | None,
    totals: dict[str, int],
    unavailable_ids: set[str],
) -> tuple[dict[str, int], set[str]]:
    for parsed in parsed_exports:
        result = import_parsed_exports(engine, (parsed,))[0]
        counts = _parsed_counts(parsed)
        totals["chunks"] += 1
        totals["short_circuited_chunks"] += int(result.short_circuited)
        totals["conflicts"] += _conflicts(result)
        totals["parse_warnings"] += len(parsed.parse_warnings)
        for name in (
            "conversations",
            "messages",
            "records",
            "assets",
            "relationships",
            "parent_relationships",
            "asset_references",
            "resolved_asset_references",
            "unavailable_asset_references",
        ):
            totals[name] += int(counts[name])
        unavailable_ids.update(counts["unavailable_ids"])
        if on_member_imported is not None:
            on_member_imported(member, parsed, result)
    if not totals["chunks"]:
        raise OpenAIAdapterError("accounted member produced no parsed exports")
    return totals, unavailable_ids


def import_openai_all(
    engine: Engine,
    source_path: str,
    *,
    on_member_start: MemberCallback | None = None,
    on_member_imported: ImportedCallback | None = None,
) -> OpenAIImportSummary:
    """Inventory and import every physical member in an official OpenAI export."""

    with OpenAIExportReader(source_path) as reader:
        inventory = _inspect(reader)
        catalog = load_asset_catalog(reader, inventory.members)
        coverage: dict[str, OpenAIFileCoverage] = {}
        seen_conversation_ids: set[str] = set()
        unavailable_asset_ids: set[str] = set()
        grand = {
            "files_processed": 0,
            "files_short_circuited": 0,
            "files_failed": 0,
            "chunks": 0,
            "conversations": 0,
            "messages": 0,
            "records": 0,
            "exported_assets": 0,
            "relationships": 0,
            "parent_relationships": 0,
            "asset_references": 0,
            "resolved_asset_references": 0,
            "unavailable_asset_references": 0,
            "conflicts": 0,
            "parse_warnings": 0,
        }
        ordered = sorted(
            (item for item in inventory.members if item.accounting_category != "unknown"),
            key=lambda item: (
                {
                    "binary_asset": 0,
                    "rendered_html": 0,
                    "manifest": 1,
                    "structured_json": 1,
                    "conversation_shard": 2,
                }[item.accounting_category],
                item.member_path,
            ),
        )
        for member in ordered:
            started = perf_counter()
            if on_member_start is not None:
                on_member_start(member)
            failures = 0
            member_totals = {
                "chunks": 0,
                "short_circuited_chunks": 0,
                "conversations": 0,
                "messages": 0,
                "records": 0,
                "assets": 0,
                "relationships": 0,
                "parent_relationships": 0,
                "asset_references": 0,
                "resolved_asset_references": 0,
                "unavailable_asset_references": 0,
                "conflicts": 0,
                "parse_warnings": 0,
            }
            member_unavailable: set[str] = set()
            try:
                if member.accounting_category in {"binary_asset", "rendered_html"}:
                    parsed_exports = (parse_asset_member(reader, member, catalog),)
                elif member.accounting_category in {"manifest", "structured_json"}:
                    parsed_exports = (parse_structured_member(reader, member, catalog),)
                else:
                    parsed_exports = iter_conversation_member_exports(
                        reader,
                        member,
                        catalog,
                        seen_conversation_ids=seen_conversation_ids,
                    )
                _import_member(
                    engine,
                    member,
                    parsed_exports,
                    on_member_imported=on_member_imported,
                    totals=member_totals,
                    unavailable_ids=member_unavailable,
                )
                grand["files_processed"] += 1
                if member_totals["short_circuited_chunks"] == member_totals["chunks"]:
                    grand["files_short_circuited"] += 1
                if member.accounting_category in {"binary_asset", "rendered_html"}:
                    grand["exported_assets"] += 1
            except Exception:  # noqa: BLE001 - isolate failures behind opaque member IDs
                failures = 1
                grand["files_failed"] += 1
            unavailable_asset_ids.update(member_unavailable)
            item = OpenAIFileCoverage(
                member_id=member.member_id,
                accounting_category=member.accounting_category,
                failures=failures,
                elapsed_seconds=perf_counter() - started,
                **member_totals,
            )
            coverage[member.member_id] = item
            for name in (
                "chunks",
                "conversations",
                "messages",
                "records",
                "relationships",
                "parent_relationships",
                "asset_references",
                "resolved_asset_references",
                "unavailable_asset_references",
                "conflicts",
                "parse_warnings",
            ):
                grand[name] += int(getattr(item, name))
        return OpenAIImportSummary(
            physical_members_total=inventory.physical_members_total,
            logical_files_total=inventory.logical_files_total,
            physical_source_bytes=inventory.physical_source_bytes,
            logical_source_bytes=inventory.logical_source_bytes,
            conversation_shards=inventory.conversation_shards,
            structured_json_files=inventory.structured_json_files + 1,
            exported_asset_files=inventory.binary_asset_files,
            rendered_html_files=inventory.rendered_html_files,
            unknown_files=inventory.unknown_files,
            unavailable_assets=len(unavailable_asset_ids),
            coverage=coverage,
            **grand,
        )
