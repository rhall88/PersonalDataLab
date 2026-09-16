from __future__ import annotations

from collections.abc import Callable
from time import perf_counter

from pydantic import BaseModel, ConfigDict
from sqlalchemy.engine import Engine

from ..database import import_parsed_exports
from ..models import ImportResult, ParsedExport
from .adapters import (
    DEFAULT_ADAPTER_REGISTRY,
    AdapterRegistry,
    GoogleTakeoutAdapter,
    open_takeout_member,
)
from .inventory import GoogleInventory, TakeoutMember, inspect_google
from .products import EXPECTED_68_PRODUCTS, PRODUCT_REGISTRY, SupportLevel

MemberCallback = Callable[[TakeoutMember, GoogleTakeoutAdapter], None]
ImportedCallback = Callable[[TakeoutMember, ParsedExport, ImportResult], None]


class ProductCoverage(BaseModel):
    model_config = ConfigDict(frozen=True)

    product: str
    product_key: str
    planned_adapter: str
    support_status: SupportLevel
    members_discovered: int = 0
    members_processed: int = 0
    members_short_circuited: int = 0
    records_parsed: int = 0
    events_parsed: int = 0
    conversations_parsed: int = 0
    assets_indexed: int = 0
    relationships_parsed: int = 0
    bytes_discovered: int = 0
    bytes_processed: int = 0
    parse_warnings: int = 0
    unsupported_variants: int = 0
    failures: int = 0
    conflicts: int = 0
    inserted: int = 0
    unchanged: int = 0
    stale: int = 0
    version_count: int = 0
    reused_versions: int = 0
    elapsed_seconds: float = 0.0
    adapters_used: tuple[str, ...] = ()
    formats_observed: tuple[str, ...] = ()
    failed_members: tuple[str, ...] = ()


class GoogleImportSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    export_id: str | None
    products_detected: int
    products_fully_supported: int
    members_total: int
    members_imported: int
    members_short_circuited: int
    members_asset_indexed: int
    members_raw_preserved: int
    members_failed: int
    members_unknown: int
    structural_members: int
    bytes_accounted_for: int
    coverage: dict[str, ProductCoverage]

    @property
    def exit_code(self) -> int:
        if self.members_failed:
            return 4
        if self.members_unknown:
            return 5
        if self.members_raw_preserved:
            return 3
        if any(item.conflicts for item in self.coverage.values()):
            return 2
        return 0


def _empty_coverage(inventory: GoogleInventory) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for key in EXPECTED_68_PRODUCTS:
        product = PRODUCT_REGISTRY[key]
        members = [member for member in inventory.members if member.product_key == key]
        files = [
            member
            for member in members
            if not member.is_directory and member.accounting_category != "structural"
        ]
        unclassified_files = sum(
            member.accounting_category == "unknown" for member in files
        )
        if files:
            state: SupportLevel = product.support_level
        elif members:
            state = "empty_in_export"
        else:
            state = "not_present_in_export"
        result[key] = {
            "product": product.takeout_display_names[0],
            "product_key": key,
            "planned_adapter": product.adapter,
            "support_status": state,
            "members_discovered": len(files),
            "members_processed": 0,
            "members_short_circuited": 0,
            "records_parsed": 0,
            "events_parsed": 0,
            "conversations_parsed": 0,
            "assets_indexed": 0,
            "relationships_parsed": 0,
            "bytes_discovered": sum(member.uncompressed_size for member in files),
            "bytes_processed": 0,
            "parse_warnings": 0,
            "unsupported_variants": 0,
            "failures": 0,
            "conflicts": 0,
            "inserted": 0,
            "unchanged": 0,
            "stale": 0,
            "version_count": 0,
            "reused_versions": 0,
            "elapsed_seconds": 0.0,
            "adapters_used": set(),
            "formats_observed": {member.extension or "[no extension]" for member in files},
            "failed_members": [],
            "_full_members": 0,
            "_partial_members": 0,
            "_raw_members": unclassified_files,
        }
    return result


def _apply_result(
    coverage: dict[str, object],
    parsed: ParsedExport,
    result: ImportResult,
    *,
    count_member: bool = True,
) -> None:
    if count_member:
        coverage["members_processed"] = int(coverage["members_processed"]) + 1
        coverage["bytes_processed"] = int(coverage["bytes_processed"]) + parsed.size_bytes
    coverage["records_parsed"] = int(coverage["records_parsed"]) + parsed.record_count
    coverage["events_parsed"] = int(coverage["events_parsed"]) + parsed.event_count
    coverage["conversations_parsed"] = (
        int(coverage["conversations_parsed"]) + parsed.conversation_count
    )
    coverage["assets_indexed"] = int(coverage["assets_indexed"]) + parsed.asset_count
    coverage["relationships_parsed"] = (
        int(coverage["relationships_parsed"]) + parsed.relationship_count
    )
    coverage["parse_warnings"] = int(coverage["parse_warnings"]) + len(
        parsed.parse_warnings
    )
    coverage["conflicts"] = int(coverage["conflicts"]) + (
        result.conflicted_conversations
        + result.conflicted_events
        + result.conflicted_records
        + result.conflicted_assets
        + result.conflicted_relationships
    )
    if result.short_circuited:
        conflicts = (
            result.conflicted_conversations
            + result.conflicted_events
            + result.conflicted_records
            + result.conflicted_assets
            + result.conflicted_relationships
        )
        source_objects = (
            result.source_conversations
            + result.source_events
            + result.source_records
            + result.source_assets
            + result.source_relationships
        )
        coverage["members_short_circuited"] = (
            int(coverage["members_short_circuited"]) + 1
        )
        coverage["unchanged"] = int(coverage["unchanged"]) + source_objects - conflicts
        coverage["reused_versions"] = int(coverage["reused_versions"]) + source_objects
        return
    coverage["inserted"] = int(coverage["inserted"]) + (
        result.inserted_conversations
        + result.inserted_events
        + result.inserted_records
        + result.inserted_assets
        + result.inserted_relationships
    )
    coverage["unchanged"] = int(coverage["unchanged"]) + (
        result.unchanged_conversations
        + result.unchanged_events
        + result.unchanged_records
        + result.unchanged_assets
        + result.unchanged_relationships
    )
    coverage["stale"] = int(coverage["stale"]) + (
        result.stale_conversations
        + result.stale_events
        + result.stale_records
        + result.stale_assets
        + result.stale_relationships
    )
    coverage["version_count"] = int(coverage["version_count"]) + result.new_distinct_versions
    coverage["reused_versions"] = int(coverage["reused_versions"]) + result.reused_versions


def _finalize_coverage(raw: dict[str, dict[str, object]]) -> dict[str, ProductCoverage]:
    result: dict[str, ProductCoverage] = {}
    for key, mutable in raw.items():
        failures = int(mutable["failures"])
        raw_members = int(mutable.pop("_raw_members"))
        full_members = int(mutable.pop("_full_members"))
        partial_members = int(mutable.pop("_partial_members"))
        discovered = int(mutable["members_discovered"])
        if failures:
            mutable["support_status"] = "failed"
        elif discovered == 0:
            pass
        elif raw_members:
            mutable["support_status"] = "raw_preserved"
        elif partial_members:
            mutable["support_status"] = "partially_structured"
        elif full_members == discovered:
            mutable["support_status"] = "fully_structured"
        mutable["adapters_used"] = tuple(sorted(mutable["adapters_used"]))
        mutable["formats_observed"] = tuple(sorted(mutable["formats_observed"]))
        mutable["failed_members"] = tuple(mutable["failed_members"])
        result[key] = ProductCoverage.model_validate(mutable)
    return result


def import_google_all(
    engine: Engine,
    source_path: str,
    *,
    registry: AdapterRegistry = DEFAULT_ADAPTER_REGISTRY,
    on_member_start: MemberCallback | None = None,
    on_member_imported: ImportedCallback | None = None,
) -> GoogleImportSummary:
    """Inventory and process one supported member per restart-safe transaction."""

    inventory = inspect_google(source_path)
    mutable = _empty_coverage(inventory)
    imported_members = 0
    short_circuited_members = 0
    raw_preserved = 0
    failed = 0
    asset_indexed_members = 0

    for member in inventory.members:
        if member.is_directory or member.accounting_category != "classified":
            continue
        key = member.product_key
        if key is None:
            continue
        coverage = mutable[key]
        started = perf_counter()
        adapter = registry.resolve(member)
        if adapter is None:
            coverage["unsupported_variants"] = int(coverage["unsupported_variants"]) + 1
            coverage["_raw_members"] = int(coverage["_raw_members"]) + 1
            raw_preserved += 1
            coverage["elapsed_seconds"] = float(coverage["elapsed_seconds"]) + (
                perf_counter() - started
            )
            continue
        cast_adapters = coverage["adapters_used"]
        assert isinstance(cast_adapters, set)
        cast_adapters.add(adapter.adapter_name)
        if on_member_start is not None:
            on_member_start(member, adapter)
        try:
            streaming_parser = getattr(adapter, "iter_parsed_exports", None)
            if callable(streaming_parser):
                parsed_exports = streaming_parser(member)
            else:
                def parse_once(selected_adapter=adapter, selected_member=member):
                    with open_takeout_member(selected_member) as stream:
                        yield selected_adapter.parse(stream, selected_member)

                parsed_exports = parse_once()
            chunks = 0
            every_chunk_short_circuited = True
            member_has_assets = False
            for parsed in parsed_exports:
                result = import_parsed_exports(engine, (parsed,))[0]
                _apply_result(coverage, parsed, result, count_member=chunks == 0)
                chunks += 1
                every_chunk_short_circuited &= result.short_circuited
                member_has_assets |= parsed.asset_count > 0
                if on_member_imported is not None:
                    on_member_imported(member, parsed, result)
            if chunks == 0:
                raise RuntimeError("adapter produced no parsed exports")
            imported_members += 1
            short_circuited_members += int(every_chunk_short_circuited)
            asset_indexed_members += int(member_has_assets)
            marker = "_full_members" if adapter.support_level == "fully_structured" else "_partial_members"
            coverage[marker] = int(coverage[marker]) + 1
        except Exception as error:  # noqa: BLE001 - member isolation is the public contract
            coverage["failures"] = int(coverage["failures"]) + 1
            coverage["failed_members"].append(
                f"{member.source_locator} ({type(error).__name__})"
            )
            failed += 1
        finally:
            coverage["elapsed_seconds"] = float(coverage["elapsed_seconds"]) + (
                perf_counter() - started
            )

    coverage = _finalize_coverage(mutable)
    detected = sum(item.members_discovered > 0 or item.support_status == "empty_in_export" for item in coverage.values())
    fully_supported = sum(
        item.members_discovered > 0 and item.support_status == "fully_structured"
        for item in coverage.values()
    )
    export_id = "+".join(inventory.export_ids) if inventory.export_ids else None
    return GoogleImportSummary(
        export_id=export_id,
        products_detected=detected,
        products_fully_supported=fully_supported,
        members_total=inventory.all_archive_members,
        members_imported=imported_members,
        members_short_circuited=short_circuited_members,
        members_asset_indexed=asset_indexed_members,
        members_raw_preserved=raw_preserved,
        members_failed=failed,
        members_unknown=inventory.unknown_members_count,
        structural_members=inventory.intentionally_ignored_structural_members,
        bytes_accounted_for=inventory.bytes_accounted_for,
        coverage=coverage,
    )
