from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal
from zipfile import BadZipFile, ZipFile

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..google_importer import _is_archive_activity_path, _takeout_identity
from .products import PRODUCT_REGISTRY, SupportLevel, product_for_display_name

GENERIC_STRUCTURED_EXTENSIONS = frozenset({".json", ".csv", ".xml"})
STRUCTURED_TEXT_EXTENSIONS = frozenset(
    {
        ".atom",
        ".csv",
        ".geojson",
        ".gpx",
        ".html",
        ".ics",
        ".json",
        ".kml",
        ".mbox",
        ".md",
        ".txt",
        ".vcf",
        ".vcard",
        ".xml",
    }
)
BINARY_EXTENSIONS = frozenset(
    {
        ".3gp",
        ".7z",
        ".aac",
        ".avi",
        ".bin",
        ".bmp",
        ".doc",
        ".docx",
        ".flac",
        ".gif",
        ".gz",
        ".heic",
        ".jpeg",
        ".jpg",
        ".kmz",
        ".m4a",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".ogg",
        ".opus",
        ".pdf",
        ".png",
        ".ppt",
        ".pptx",
        ".rar",
        ".tar",
        ".tif",
        ".tiff",
        ".wav",
        ".webm",
        ".webp",
        ".xls",
        ".xlsx",
        ".zip",
    }
)
STRUCTURAL_FILENAMES = frozenset({"archive_browser.html", "archive_browser.json"})
MAX_GENERIC_MEMBER_BYTES = 64 * 1024 * 1024
_ARCHIVE_ORDINAL = re.compile(
    r"^takeout-\d{8}T\d{6}Z(?P<suffix>(?:-\d+)*)"
    r"(?: \([1-9]\d*\))?\.zip$",
    re.IGNORECASE,
)


class GoogleInventoryError(ValueError):
    """Raised when a Takeout container cannot be inventoried safely."""


class TakeoutMember(BaseModel):
    model_config = ConfigDict(frozen=True)

    export_id: str | None
    exported_at: datetime | None
    archive_filename: str
    archive_path: str
    archive_ordinal: int | None
    product: str | None
    product_key: str | None
    member_path: str
    product_relative_path: str | None
    filename: str
    extension: str
    uncompressed_size: int = Field(ge=0)
    compressed_size: int | None = Field(default=None, ge=0)
    zip_crc: int | None = Field(default=None, ge=0)
    is_directory: bool
    candidate_adapter: str | None
    support_status: SupportLevel
    requires_byte_level_inspection: bool
    appears_binary: bool
    appears_structured_text: bool
    accounting_category: Literal["classified", "structural", "unknown"]
    reason: str | None = None
    container_kind: Literal["zip", "extracted"]

    @property
    def source_locator(self) -> str:
        if self.container_kind == "zip":
            return f"{self.archive_path}!/{self.member_path}"
        return str(Path(self.archive_path) / PurePosixPath(self.member_path))


class ProductInventory(BaseModel):
    model_config = ConfigDict(frozen=True)

    product: str
    product_key: str
    members: int
    files: int
    directories: int
    bytes: int
    candidate_adapters: tuple[str, ...]
    support_statuses: tuple[SupportLevel, ...]


class GoogleInventory(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_path: str
    export_ids: tuple[str, ...]
    inferred_export_timestamps: tuple[datetime, ...]
    archives: int
    all_archive_members: int
    classified_members: int
    intentionally_ignored_structural_members: int
    unknown_members_count: int
    bytes_accounted_for: int
    products: tuple[ProductInventory, ...]
    members: tuple[TakeoutMember, ...]
    unknown_members: tuple[TakeoutMember, ...]

    @model_validator(mode="after")
    def require_complete_accounting(self) -> GoogleInventory:
        accounted = (
            self.classified_members
            + self.intentionally_ignored_structural_members
            + self.unknown_members_count
        )
        if accounted != self.all_archive_members or len(self.members) != self.all_archive_members:
            raise ValueError("Google inventory does not account for every source member")
        if sum(member.uncompressed_size for member in self.members) != self.bytes_accounted_for:
            raise ValueError("Google inventory byte accounting is incomplete")
        return self

    @property
    def accounted_members(self) -> int:
        return (
            self.classified_members
            + self.intentionally_ignored_structural_members
            + self.unknown_members_count
        )


def _archive_ordinal(filename: str) -> int | None:
    match = _ARCHIVE_ORDINAL.fullmatch(filename)
    suffix = match.group("suffix") if match else ""
    return int(suffix.rsplit("-", 1)[-1]) if suffix else None


def _safe_member_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    return (
        bool(normalized)
        and "\x00" not in normalized
        and not path.is_absolute()
        and not re.match(r"^[a-zA-Z]:", normalized)
        and ".." not in path.parts
    )


def _product_context(member_path: str) -> tuple[str | None, str | None, str | None]:
    parts = PurePosixPath(member_path.replace("\\", "/")).parts
    if not parts:
        return None, None, None
    if parts[0].casefold() == "takeout":
        if len(parts) < 2:
            return None, None, None
        product_index = 1
    else:
        product_index = 0
    display = parts[product_index]
    product = product_for_display_name(display)
    if product is None:
        return display, None, None
    relative = PurePosixPath(*parts[product_index + 1 :]).as_posix()
    return display, product.canonical_product_key, relative if relative != "." else ""


def _classify_member(
    *,
    member_path: str,
    size_bytes: int,
    is_directory: bool,
) -> dict[str, object]:
    filename = PurePosixPath(member_path.replace("\\", "/")).name
    extension = Path(filename).suffix.casefold()
    display, product_key, relative = _product_context(member_path)
    binary = extension in BINARY_EXTENSIONS
    structured = extension in STRUCTURED_TEXT_EXTENSIONS
    safe = _safe_member_path(member_path)
    base: dict[str, object] = {
        "product": display,
        "product_key": product_key,
        "product_relative_path": relative,
        "filename": filename,
        "extension": extension,
        "appears_binary": binary,
        "appears_structured_text": structured,
    }
    if not safe:
        return base | {
            "candidate_adapter": None,
            "support_status": "raw_preserved",
            "requires_byte_level_inspection": False,
            "accounting_category": "unknown",
            "reason": "unsafe_archive_path",
        }
    if product_key is None and display is not None and is_directory:
        return base | {
            "candidate_adapter": None,
            "support_status": "raw_preserved",
            "requires_byte_level_inspection": False,
            "accounting_category": "unknown",
            "reason": "unknown_product_directory",
        }
    if is_directory or filename.casefold() in STRUCTURAL_FILENAMES:
        return base | {
            "candidate_adapter": None,
            "support_status": "raw_preserved",
            "requires_byte_level_inspection": False,
            "accounting_category": "structural",
            "reason": "directory" if is_directory else "takeout_structural_member",
        }
    if product_key is None:
        return base | {
            "candidate_adapter": None,
            "support_status": "raw_preserved",
            "requires_byte_level_inspection": not binary,
            "accounting_category": "unknown",
            "reason": "unknown_product_or_root_member",
        }
    if _is_archive_activity_path(member_path):
        return base | {
            "candidate_adapter": "my_activity",
            "support_status": "fully_structured",
            "requires_byte_level_inspection": True,
            "accounting_category": "classified",
            "reason": None,
        }
    if product_key == "my_activity":
        return base | {
            "candidate_adapter": None,
            "support_status": "raw_preserved",
            "requires_byte_level_inspection": not (binary or structured),
            "accounting_category": "classified",
            "reason": "no_pr5_activity_pattern",
        }
    if (
        extension in GENERIC_STRUCTURED_EXTENSIONS
        and size_bytes <= MAX_GENERIC_MEMBER_BYTES
    ):
        return base | {
            "candidate_adapter": "generic_structured",
            "support_status": "partially_structured",
            "requires_byte_level_inspection": True,
            "accounting_category": "classified",
            "reason": None,
        }
    product = PRODUCT_REGISTRY[product_key]
    reason = (
        "generic_member_exceeds_64_mib_limit"
        if extension in GENERIC_STRUCTURED_EXTENSIONS
        else "no_pr5_semantic_adapter"
    )
    return base | {
        "candidate_adapter": product.adapter,
        "support_status": product.support_level,
        "requires_byte_level_inspection": not (binary or structured),
        "accounting_category": "classified",
        "reason": reason,
    }


def _zip_members(path: Path) -> list[TakeoutMember]:
    exported_at, export_id = _takeout_identity(path)
    ordinal = _archive_ordinal(path.name)
    members: list[TakeoutMember] = []
    try:
        with ZipFile(path) as archive:
            for info in archive.infolist():
                classification = _classify_member(
                    member_path=info.filename,
                    size_bytes=info.file_size,
                    is_directory=info.is_dir(),
                )
                members.append(
                    TakeoutMember(
                        export_id=export_id,
                        exported_at=exported_at,
                        archive_filename=path.name,
                        archive_path=str(path),
                        archive_ordinal=ordinal,
                        member_path=info.filename,
                        uncompressed_size=info.file_size,
                        compressed_size=info.compress_size,
                        zip_crc=info.CRC,
                        is_directory=info.is_dir(),
                        container_kind="zip",
                        **classification,
                    )
                )
    except (BadZipFile, OSError) as error:
        raise GoogleInventoryError(f"invalid or unreadable ZIP archive: {path}") from error
    return members


def _extracted_members(root: Path) -> list[TakeoutMember]:
    exported_at, export_id = _takeout_identity(root)
    takeout = root / "Takeout"
    if takeout.is_dir():
        scan_root = takeout
        container_root = root
        prefix = PurePosixPath("Takeout")
    elif root.name.casefold() == "takeout":
        scan_root = root
        container_root = root.parent
        prefix = PurePosixPath("Takeout")
    else:
        scan_root = root
        container_root = root
        prefix = PurePosixPath()
    members: list[TakeoutMember] = []
    for item in sorted(scan_root.rglob("*"), key=lambda value: str(value).casefold()):
        relative = item.relative_to(scan_root)
        member_path = (prefix / PurePosixPath(relative.as_posix())).as_posix()
        is_symlink = item.is_symlink()
        is_directory = item.is_dir() if not is_symlink else False
        size_bytes = 0 if is_directory else item.lstat().st_size
        classification = _classify_member(
            member_path=member_path,
            size_bytes=size_bytes,
            is_directory=is_directory,
        )
        if is_symlink:
            classification |= {
                "candidate_adapter": None,
                "support_status": "raw_preserved",
                "requires_byte_level_inspection": False,
                "accounting_category": "unknown",
                "reason": "extracted_symlink_not_followed",
            }
        members.append(
            TakeoutMember(
                export_id=export_id,
                exported_at=exported_at,
                archive_filename=root.name,
                archive_path=str(container_root),
                archive_ordinal=None,
                member_path=member_path,
                uncompressed_size=size_bytes,
                compressed_size=None,
                zip_crc=None,
                is_directory=is_directory,
                container_kind="extracted",
                **classification,
            )
        )
    return members


def _product_summaries(members: tuple[TakeoutMember, ...]) -> tuple[ProductInventory, ...]:
    grouped: dict[str, list[TakeoutMember]] = defaultdict(list)
    for member in members:
        if member.product_key is not None:
            grouped[member.product_key].append(member)
    summaries: list[ProductInventory] = []
    for key, items in grouped.items():
        product = PRODUCT_REGISTRY[key]
        summaries.append(
            ProductInventory(
                product=product.takeout_display_names[0],
                product_key=key,
                members=len(items),
                files=sum(not item.is_directory for item in items),
                directories=sum(item.is_directory for item in items),
                bytes=sum(item.uncompressed_size for item in items),
                candidate_adapters=tuple(
                    sorted({item.candidate_adapter for item in items if item.candidate_adapter})
                ),
                support_statuses=tuple(sorted({item.support_status for item in items})),
            )
        )
    return tuple(sorted(summaries, key=lambda item: item.product.casefold()))


def inspect_google(source_path: str | Path) -> GoogleInventory:
    """Inventory a Takeout source without extracting archives or parsing member payloads."""

    path = Path(source_path).expanduser().resolve(strict=True)
    if path.is_file():
        if path.suffix.casefold() != ".zip":
            raise GoogleInventoryError("inspect-google requires a Takeout ZIP or directory")
        archive_paths = (path,)
        raw_members = _zip_members(path)
    elif path.is_dir():
        archive_paths = tuple(
            sorted(
                (
                    item
                    for item in path.iterdir()
                    if item.is_file()
                    and item.suffix.casefold() == ".zip"
                    and item.name.casefold().startswith("takeout-")
                ),
                key=lambda item: item.name.casefold(),
            )
        )
        if archive_paths:
            raw_members = [member for archive in archive_paths for member in _zip_members(archive)]
        else:
            raw_members = _extracted_members(path)
            archive_paths = (path,)
    else:
        raise GoogleInventoryError(f"unsupported Takeout source: {path}")

    members = tuple(raw_members)
    unknown = tuple(item for item in members if item.accounting_category == "unknown")
    export_ids = tuple(sorted({item.export_id for item in members if item.export_id}))
    timestamps = tuple(sorted({item.exported_at for item in members if item.exported_at}))
    return GoogleInventory(
        source_path=str(path),
        export_ids=export_ids,
        inferred_export_timestamps=timestamps,
        archives=len(archive_paths),
        all_archive_members=len(members),
        classified_members=sum(item.accounting_category == "classified" for item in members),
        intentionally_ignored_structural_members=sum(
            item.accounting_category == "structural" for item in members
        ),
        unknown_members_count=len(unknown),
        bytes_accounted_for=sum(item.uncompressed_size for item in members),
        products=_product_summaries(members),
        members=members,
        unknown_members=unknown,
    )


def _human_size(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{value} B"
        amount /= 1024
    raise AssertionError("unreachable")


def render_inventory_table(inventory: GoogleInventory) -> str:
    rows = [("Product", "Members", "Size", "Adapter / disposition")]
    for product in inventory.products:
        adapter = ", ".join(product.candidate_adapters) or ", ".join(product.support_statuses)
        rows.append((product.product, f"{product.members:,}", _human_size(product.bytes), adapter))
    if inventory.unknown_members_count:
        unknown_bytes = sum(item.uncompressed_size for item in inventory.unknown_members)
        rows.append(("Unknown", f"{inventory.unknown_members_count:,}", _human_size(unknown_bytes), "inspect"))
    widths = [max(len(row[index]) for row in rows) for index in range(4)]
    lines = ["Google Takeout Inventory", ""]
    for index, row in enumerate(rows):
        lines.append("  ".join(value.ljust(widths[column]) for column, value in enumerate(row)))
        if index == 0:
            lines.append("  ".join("-" * width for width in widths))
    lines.extend(
        [
            "",
            f"Accounted members: {inventory.accounted_members:,}/{inventory.all_archive_members:,}",
            f"Accounted bytes: {_human_size(inventory.bytes_accounted_for)}",
        ]
    )
    return "\n".join(lines)
