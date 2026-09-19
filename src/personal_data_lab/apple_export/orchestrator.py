from __future__ import annotations

from collections.abc import Callable, Iterator
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, computed_field
from sqlalchemy.engine import Engine

from ..database import import_parsed_exports
from ..models import ImportResult, ParsedExport
from .adapters import ADAPTERS, AppleAdapter, AppleAdapterError
from .inventory import AppleInventory, AppleMember, inspect_apple, open_apple_member
from .products import PRODUCT_REGISTRY

AppleInterpretationStatus = Literal[
    "provider_specific", "generic_lossless", "not_present_in_export", "failed"
]
AppleRepresentationStatus = Literal[
    "fully_structured",
    "losslessly_structured",
    "partially_structured",
    "not_present_in_export",
    "failed",
]


class AppleProductCoverage(BaseModel):
    model_config = ConfigDict(frozen=True)

    product: str
    product_key: str
    status: AppleRepresentationStatus
    interpretation_status: AppleInterpretationStatus
    members_discovered: int = 0
    members_processed: int = 0
    members_short_circuited: int = 0
    bytes_discovered: int = 0
    bytes_processed: int = 0
    records: int = 0
    events: int = 0
    assets: int = 0
    relationships: int = 0
    parse_warnings: int = 0
    conflicts: int = 0
    failures: int = 0
    adapters: tuple[str, ...] = ()
    formats: tuple[str, ...] = ()
    failed_member_ids: tuple[str, ...] = ()
    elapsed_seconds: float = 0.0


class AppleImportSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    outer_archives: int
    physical_source_bytes: int
    recursive_entries: int
    data_members_total: int
    members_imported: int
    members_short_circuited: int
    members_failed: int
    members_unknown: int
    structural_members: int
    guide_members: int
    nested_archive_containers: int
    logical_leaf_bytes: int
    data_bytes_processed: int
    coverage: dict[str, AppleProductCoverage]

    @computed_field
    @property
    def complete_accounting(self) -> bool:
        return (
            self.members_imported + self.members_failed + self.members_unknown
            == self.data_members_total + self.members_unknown
            and self.recursive_entries
            == self.data_members_total + self.structural_members + self.members_unknown
        )

    @computed_field
    @property
    def production_ready(self) -> bool:
        return bool(
            self.complete_accounting
            and not self.members_failed
            and not self.members_unknown
            and not any(item.conflicts or item.parse_warnings for item in self.coverage.values())
            and all(
                item.status
                in {
                    "fully_structured",
                    "losslessly_structured",
                    "not_present_in_export",
                }
                for item in self.coverage.values()
            )
        )

    @computed_field
    @property
    def exit_code(self) -> int:
        if self.members_failed:
            return 4
        if self.members_unknown:
            return 5
        if any(item.conflicts for item in self.coverage.values()):
            return 2
        if any(
            item.parse_warnings or item.status == "partially_structured"
            for item in self.coverage.values()
        ):
            return 3
        return 0


MemberCallback = Callable[[AppleMember, AppleAdapter], None]
ImportedCallback = Callable[[AppleMember, ParsedExport, ImportResult], None]


def _coverage(inventory: AppleInventory) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    summaries = {item.product_key: item for item in inventory.products}
    for key, product in PRODUCT_REGISTRY.items():
        observed = summaries.get(key)
        result[key] = {
            "product": product.display_name,
            "product_key": key,
            "status": "pending" if observed else "not_present_in_export",
            "interpretation_status": "pending" if observed else "not_present_in_export",
            "members_discovered": observed.data_members if observed else 0,
            "members_processed": 0,
            "members_short_circuited": 0,
            "bytes_discovered": observed.logical_bytes if observed else 0,
            "bytes_processed": 0,
            "records": 0,
            "events": 0,
            "assets": 0,
            "relationships": 0,
            "parse_warnings": 0,
            "conflicts": 0,
            "failures": 0,
            "adapters": set(),
            "interpretation_levels": set(),
            "formats": set(observed.formats if observed else ()),
            "failed_member_ids": [],
            "elapsed_seconds": 0.0,
        }
    return result


def _parsed_exports(adapter: AppleAdapter, member: AppleMember) -> Iterator[ParsedExport]:
    streaming = getattr(adapter, "iter_parsed_exports", None)
    if callable(streaming):
        yield from streaming(member)
        return
    with open_apple_member(member) as stream:
        yield adapter.parse(stream, member)


def _conflicts(result: ImportResult) -> int:
    return (
        result.conflicted_conversations
        + result.conflicted_events
        + result.conflicted_records
        + result.conflicted_assets
        + result.conflicted_relationships
    )


def import_apple_all(
    engine: Engine,
    source_path: str,
    *,
    on_member_start: MemberCallback | None = None,
    on_member_imported: ImportedCallback | None = None,
) -> AppleImportSummary:
    """Inventory and import each leaf (or bounded CSV chunk) in a restart-safe transaction."""

    inventory = inspect_apple(source_path)
    mutable = _coverage(inventory)
    imported = 0
    short_circuited = 0
    failed = 0
    processed_bytes = 0
    for member in inventory.members:
        if member.accounting_category != "classified":
            continue
        coverage = mutable[member.product_key]
        started = perf_counter()
        adapter = ADAPTERS.get(member.candidate_adapter or "")
        if adapter is None:
            coverage["failures"] = int(coverage["failures"]) + 1
            coverage["status"] = "failed"
            coverage["failed_member_ids"].append(member.member_id)
            failed += 1
            continue
        coverage["adapters"].add(adapter.adapter_name)
        coverage["interpretation_levels"].add(adapter.interpretation_level)
        if on_member_start is not None:
            on_member_start(member, adapter)
        try:
            chunks = 0
            all_short_circuited = True
            for parsed in _parsed_exports(adapter, member):
                result = import_parsed_exports(engine, (parsed,))[0]
                chunks += 1
                all_short_circuited &= result.short_circuited
                coverage["records"] = int(coverage["records"]) + parsed.record_count
                coverage["events"] = int(coverage["events"]) + parsed.event_count
                coverage["assets"] = int(coverage["assets"]) + parsed.asset_count
                coverage["relationships"] = (
                    int(coverage["relationships"]) + parsed.relationship_count
                )
                coverage["parse_warnings"] = int(coverage["parse_warnings"]) + len(
                    parsed.parse_warnings
                )
                coverage["conflicts"] = int(coverage["conflicts"]) + _conflicts(result)
                if on_member_imported is not None:
                    on_member_imported(member, parsed, result)
            if chunks == 0:
                raise AppleAdapterError("adapter produced no parsed exports")
            if coverage["status"] != "failed":
                if (
                    adapter.support_level
                    not in {"fully_structured", "losslessly_structured"}
                    or coverage["parse_warnings"]
                    or coverage["status"] == "partially_structured"
                ):
                    coverage["status"] = "partially_structured"
                elif (
                    adapter.support_level == "losslessly_structured"
                    or coverage["status"] == "losslessly_structured"
                ):
                    coverage["status"] = "losslessly_structured"
                else:
                    coverage["status"] = "fully_structured"
            imported += 1
            processed_bytes += member.uncompressed_size
            coverage["members_processed"] = int(coverage["members_processed"]) + 1
            coverage["bytes_processed"] = (
                int(coverage["bytes_processed"]) + member.uncompressed_size
            )
            if all_short_circuited:
                short_circuited += 1
                coverage["members_short_circuited"] = int(coverage["members_short_circuited"]) + 1
        except Exception:  # noqa: BLE001 - member failure isolation is the CLI contract
            coverage["failures"] = int(coverage["failures"]) + 1
            coverage["status"] = "failed"
            coverage["failed_member_ids"].append(member.member_id)
            failed += 1
        finally:
            coverage["elapsed_seconds"] = float(coverage["elapsed_seconds"]) + (
                perf_counter() - started
            )
    finalized: dict[str, AppleProductCoverage] = {}
    for key, values in mutable.items():
        interpretation_levels = values.pop("interpretation_levels")
        if values["status"] == "failed":
            values["interpretation_status"] = "failed"
        elif values["status"] == "not_present_in_export":
            values["interpretation_status"] = "not_present_in_export"
        elif "generic_lossless" in interpretation_levels:
            values["interpretation_status"] = "generic_lossless"
        else:
            values["interpretation_status"] = "provider_specific"
        values["adapters"] = tuple(sorted(values["adapters"]))
        values["formats"] = tuple(sorted(values["formats"]))
        values["failed_member_ids"] = tuple(values["failed_member_ids"])
        finalized[key] = AppleProductCoverage.model_validate(values)
    return AppleImportSummary(
        outer_archives=inventory.outer_archives,
        physical_source_bytes=inventory.physical_source_bytes,
        recursive_entries=inventory.recursive_entries,
        data_members_total=inventory.classified_data_members,
        members_imported=imported,
        members_short_circuited=short_circuited,
        members_failed=failed,
        members_unknown=inventory.unknown_members_count,
        structural_members=inventory.structural_members,
        guide_members=inventory.guide_members,
        nested_archive_containers=inventory.nested_archive_containers,
        logical_leaf_bytes=inventory.logical_leaf_bytes,
        data_bytes_processed=processed_bytes,
        coverage=finalized,
    )
