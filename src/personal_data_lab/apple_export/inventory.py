from __future__ import annotations

import hashlib
import re
import tempfile
from collections import defaultdict
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal
from zipfile import BadZipFile, ZipFile, ZipInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .products import PRODUCT_REGISTRY, AppleProduct, product_for

STRUCTURED_EXTENSIONS = frozenset({".csv", ".json", ".xml", ".ics", ".vcf"})
TEXT_EXTENSIONS = frozenset({".txt", ""})
ASSET_EXTENSIONS = frozenset(
    {".m4a", ".mov", ".jpeg", ".jpg", ".png", ".pdf", ".heic", ".mp3", ".wav"}
)
SUPPORTED_EXTENSIONS = STRUCTURED_EXTENSIONS | TEXT_EXTENSIONS | ASSET_EXTENSIONS
SPOOL_LIMIT_BYTES = 8 * 1024 * 1024
MAX_NESTING_DEPTH = 8

_GUIDE_ARCHIVES = frozenset(
    {
        "amp-app-stores.zip",
        "app-install-activity.zip",
        "apple-id.zip",
        "apple-online-and-retail-stores.zip",
        "apple-pay-activity.zip",
        "applecare-support-activity.zip",
        "feedback-assistant-activity.zip",
        "icloud-bookmarks.zip",
        "icloud-calendars-and-reminders.zip",
        "icloud-notes.zip",
        "marketing-communications.zip",
        "other-data.zip",
    }
)


class AppleInventoryError(ValueError):
    """Raised when an Apple privacy export cannot be inventoried safely."""


class AppleMember(BaseModel):
    model_config = ConfigDict(frozen=True)

    member_id: str
    outer_archive_filename: str
    outer_archive_path: str = Field(exclude=True)
    container_chain: tuple[str, ...]
    container_entry_indices: tuple[int, ...]
    member_path: str
    entry_index: int = Field(ge=0)
    filename: str
    extension: str
    product: str
    product_key: str
    uncompressed_size: int = Field(ge=0)
    compressed_size: int | None = Field(default=None, ge=0)
    zip_crc: int | None = Field(default=None, ge=0)
    depth: int = Field(ge=0)
    is_directory: bool
    is_nested_archive: bool
    is_guide: bool
    candidate_adapter: str | None
    accounting_category: Literal["classified", "structural", "unknown"]
    reason: str | None = None

    @property
    def source_locator(self) -> str:
        chain = "!/".join((*self.container_chain, self.member_path))
        chain = f"{chain}#entry={self.entry_index}"
        return f"{self.outer_archive_path}!/{chain}"

    @property
    def is_leaf(self) -> bool:
        return not self.is_directory and not self.is_nested_archive


class AppleProductInventory(BaseModel):
    model_config = ConfigDict(frozen=True)

    product: str
    product_key: str
    data_members: int
    logical_bytes: int
    adapters: tuple[str, ...]
    formats: tuple[str, ...]


class AppleInventory(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_path: str
    outer_archives: int
    physical_source_bytes: int
    recursive_entries: int
    nested_archive_containers: int
    guide_members: int
    classified_data_members: int
    structural_members: int
    unknown_members_count: int
    logical_leaf_bytes: int
    recursive_uncompressed_bytes: int
    products: tuple[AppleProductInventory, ...]
    members: tuple[AppleMember, ...]
    unknown_members: tuple[AppleMember, ...]

    @model_validator(mode="after")
    def require_complete_accounting(self) -> AppleInventory:
        if (
            self.classified_data_members + self.structural_members + self.unknown_members_count
            != self.recursive_entries
        ):
            raise ValueError("Apple inventory does not account for every recursive entry")
        if (
            sum(item.uncompressed_size for item in self.members)
            != self.recursive_uncompressed_bytes
        ):
            raise ValueError("Apple recursive byte accounting is incomplete")
        if (
            sum(item.uncompressed_size for item in self.members if item.is_leaf)
            != self.logical_leaf_bytes
        ):
            raise ValueError("Apple logical leaf-byte accounting is incomplete")
        return self

    @property
    def accounted_members(self) -> int:
        return self.classified_data_members + self.structural_members + self.unknown_members_count

    def public_dict(self, *, include_paths: bool = False) -> dict[str, object]:
        data = self.model_dump(mode="json", exclude={"members", "unknown_members"})
        fields = {
            "member_id",
            "product",
            "product_key",
            "extension",
            "uncompressed_size",
            "depth",
            "is_nested_archive",
            "is_guide",
            "candidate_adapter",
            "accounting_category",
            "reason",
        }
        if include_paths:
            fields |= {"outer_archive_filename", "container_chain", "member_path", "filename"}
        data["members"] = [item.model_dump(mode="json", include=fields) for item in self.members]
        data["unknown_members"] = [
            item.model_dump(mode="json", include=fields) for item in self.unknown_members
        ]
        return data


def _safe_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    return (
        bool(normalized)
        and "\x00" not in normalized
        and not path.is_absolute()
        and not re.match(r"^[a-zA-Z]:", normalized)
        and ".." not in path.parts
    )


def _is_guide(outer_archive: str, member_path: str, extension: str) -> bool:
    if extension != ".pdf":
        return False
    lowered = outer_archive.casefold()
    normalized = member_path.casefold()
    return (
        lowered in _GUIDE_ARCHIVES
        or lowered.startswith("amp-")
        or "guide" in normalized
        or "data and privacy" in normalized
    )


def _adapter_for(product: AppleProduct, path: str, extension: str) -> str | None:
    normalized = path.casefold().replace("\\", "/")
    if extension == ".vcf":
        return "apple_contacts"
    if extension == ".ics":
        return "apple_calendar"
    if product.key == "notes" and extension == ".txt" and "/notes/" in f"/{normalized}":
        return "apple_notes"
    if extension in {".csv", ".json", ".xml"}:
        return "apple_structured"
    if extension in ASSET_EXTENSIONS:
        return "apple_asset"
    if extension in TEXT_EXTENSIONS:
        return "apple_text"
    return None


def _member(
    *,
    outer_path: Path,
    chain: tuple[str, ...],
    chain_indices: tuple[int, ...],
    info: ZipInfo,
    entry_index: int,
) -> AppleMember:
    path = info.filename.replace("\\", "/")
    extension = Path(PurePosixPath(path).name).suffix.casefold()
    nested = extension == ".zip" and not info.is_dir()
    guide = _is_guide(outer_path.name, path, extension)
    product = product_for(outer_path.name, "/".join((*chain, path)))
    safe = _safe_path(path) and all(_safe_path(item) for item in chain)
    adapter = None if nested or guide or info.is_dir() else _adapter_for(product, path, extension)
    if not safe:
        category: Literal["classified", "structural", "unknown"] = "unknown"
        reason = "unsafe_archive_path"
    elif info.is_dir():
        category = "structural"
        reason = "directory"
    elif nested:
        category = "structural"
        reason = "nested_archive_container"
    elif guide:
        category = "structural"
        reason = "provider_guide"
    elif extension not in SUPPORTED_EXTENSIONS or adapter is None:
        category = "unknown"
        reason = "unsupported_leaf_format"
    else:
        category = "classified"
        reason = None
    identity = "\x00".join(
        (
            outer_path.name,
            *(f"{name}#{index}" for name, index in zip(chain, chain_indices, strict=True)),
            f"{path}#{entry_index}",
        )
    ).encode("utf-8")
    return AppleMember(
        member_id=hashlib.sha256(identity).hexdigest()[:20],
        outer_archive_filename=outer_path.name,
        outer_archive_path=str(outer_path),
        container_chain=chain,
        container_entry_indices=chain_indices,
        member_path=path,
        entry_index=entry_index,
        filename=PurePosixPath(path).name,
        extension=extension,
        product=product.display_name,
        product_key=product.key,
        uncompressed_size=info.file_size,
        compressed_size=info.compress_size,
        zip_crc=info.CRC,
        depth=len(chain),
        is_directory=info.is_dir(),
        is_nested_archive=nested,
        is_guide=guide,
        candidate_adapter=adapter,
        accounting_category=category,
        reason=reason,
    )


def _inventory_archive(
    archive: ZipFile,
    outer_path: Path,
    chain: tuple[str, ...],
    chain_indices: tuple[int, ...],
    output: list[AppleMember],
) -> None:
    if len(chain) > MAX_NESTING_DEPTH:
        raise AppleInventoryError(f"nested ZIP depth exceeds {MAX_NESTING_DEPTH}")
    for entry_index, info in enumerate(archive.infolist()):
        item = _member(
            outer_path=outer_path,
            chain=chain,
            chain_indices=chain_indices,
            info=info,
            entry_index=entry_index,
        )
        output.append(item)
        if not item.is_nested_archive or item.accounting_category == "unknown":
            continue
        try:
            with (
                archive.open(info) as source,
                tempfile.SpooledTemporaryFile(max_size=SPOOL_LIMIT_BYTES) as nested_file,
            ):
                while chunk := source.read(1024 * 1024):
                    nested_file.write(chunk)
                nested_file.seek(0)
                with ZipFile(nested_file) as nested:
                    _inventory_archive(
                        nested,
                        outer_path,
                        (*chain, info.filename),
                        (*chain_indices, entry_index),
                        output,
                    )
        except (BadZipFile, OSError) as error:
            raise AppleInventoryError(
                f"invalid nested ZIP in archive member {item.member_id}"
            ) from error


def _product_summaries(members: tuple[AppleMember, ...]) -> tuple[AppleProductInventory, ...]:
    grouped: dict[str, list[AppleMember]] = defaultdict(list)
    for member in members:
        if member.accounting_category == "classified":
            grouped[member.product_key].append(member)
    return tuple(
        AppleProductInventory(
            product=PRODUCT_REGISTRY[key].display_name,
            product_key=key,
            data_members=len(items),
            logical_bytes=sum(item.uncompressed_size for item in items),
            adapters=tuple(
                sorted({item.candidate_adapter for item in items if item.candidate_adapter})
            ),
            formats=tuple(sorted({item.extension or "<none>" for item in items})),
        )
        for key, items in sorted(grouped.items())
    )


def inspect_apple(source_path: str | Path) -> AppleInventory:
    """Recursively inventory official Apple export ZIPs without extracting or mutating them."""

    path = Path(source_path).expanduser().resolve(strict=True)
    if path.is_file():
        archives = (path,) if path.suffix.casefold() == ".zip" else ()
    elif path.is_dir():
        archives = tuple(
            sorted(
                (
                    item.resolve()
                    for item in path.iterdir()
                    if item.is_file() and item.suffix.casefold() == ".zip"
                ),
                key=lambda item: item.name.casefold(),
            )
        )
    else:
        archives = ()
    if not archives:
        raise AppleInventoryError("inspect-apple requires an Apple export ZIP or directory of ZIPs")
    members: list[AppleMember] = []
    for archive_path in archives:
        try:
            with ZipFile(archive_path) as archive:
                _inventory_archive(archive, archive_path, (), (), members)
        except (BadZipFile, OSError) as error:
            raise AppleInventoryError(
                f"invalid or unreadable ZIP archive: {archive_path}"
            ) from error
    all_members = tuple(members)
    unknown = tuple(item for item in all_members if item.accounting_category == "unknown")
    leaves = tuple(item for item in all_members if item.is_leaf)
    return AppleInventory(
        source_path=str(path),
        outer_archives=len(archives),
        physical_source_bytes=sum(item.stat().st_size for item in archives),
        recursive_entries=len(all_members),
        nested_archive_containers=sum(item.is_nested_archive for item in all_members),
        guide_members=sum(item.is_guide for item in all_members),
        classified_data_members=sum(
            item.accounting_category == "classified" for item in all_members
        ),
        structural_members=sum(item.accounting_category == "structural" for item in all_members),
        unknown_members_count=len(unknown),
        logical_leaf_bytes=sum(item.uncompressed_size for item in leaves),
        recursive_uncompressed_bytes=sum(item.uncompressed_size for item in all_members),
        products=_product_summaries(all_members),
        members=all_members,
        unknown_members=unknown,
    )


@contextmanager
def open_apple_member(member: AppleMember) -> Iterator[BinaryIO]:
    """Open a leaf member through an arbitrary nested ZIP chain."""

    with ExitStack() as stack:
        archive: ZipFile = stack.enter_context(ZipFile(member.outer_archive_path))
        for nested_index in member.container_entry_indices:
            nested_info = archive.infolist()[nested_index]
            source = stack.enter_context(archive.open(nested_info))
            spool = stack.enter_context(tempfile.SpooledTemporaryFile(max_size=SPOOL_LIMIT_BYTES))
            while chunk := source.read(1024 * 1024):
                spool.write(chunk)
            spool.seek(0)
            archive = stack.enter_context(ZipFile(spool))
        info = archive.infolist()[member.entry_index]
        if info.filename.replace("\\", "/") != member.member_path:
            raise AppleInventoryError("Apple member entry index no longer matches inventory")
        stream = stack.enter_context(archive.open(info))
        yield stream


def _human_size(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{value} B"
        amount /= 1024
    raise AssertionError("unreachable")


def render_inventory_table(inventory: AppleInventory) -> str:
    rows = [("Product", "Data files", "Logical size", "Adapters")]
    for product in inventory.products:
        rows.append(
            (
                product.product,
                f"{product.data_members:,}",
                _human_size(product.logical_bytes),
                ", ".join(product.adapters),
            )
        )
    widths = [max(len(row[index]) for row in rows) for index in range(4)]
    lines = ["Apple Privacy Export Inventory", ""]
    for index, row in enumerate(rows):
        lines.append("  ".join(value.ljust(widths[column]) for column, value in enumerate(row)))
        if index == 0:
            lines.append("  ".join("-" * width for width in widths))
    lines.extend(
        [
            "",
            f"Accounted entries: {inventory.accounted_members:,}/{inventory.recursive_entries:,}",
            f"Data files: {inventory.classified_data_members:,}",
            f"Guide PDFs: {inventory.guide_members:,}",
            f"Nested ZIP containers: {inventory.nested_archive_containers:,}",
            f"Physical source size: {_human_size(inventory.physical_source_bytes)}",
            f"Logical leaf size: {_human_size(inventory.logical_leaf_bytes)}",
        ]
    )
    return "\n".join(lines)
