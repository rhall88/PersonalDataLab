from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal
from zipfile import BadZipFile, ZipFile, ZipInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .products import PRODUCT_REGISTRY, THREAD_CONTAINERS, product_for

STRUCTURED_EXTENSIONS = frozenset({".json"})
ASSET_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".mp4", ".wav", ".docx"})
SUPPORTED_EXTENSIONS = STRUCTURED_EXTENSIONS | ASSET_EXTENSIONS
MAX_TEXT_BYTES = 64 * 1024 * 1024

_MESSAGE_JSON = re.compile(
    r"^your_facebook_activity/messages/(?:"
    + "|".join(re.escape(container) for container in sorted(THREAD_CONTAINERS))
    + r")/[^/]+/message_\d+\.json$"
)


class FacebookInventoryError(ValueError):
    """Raised when a Facebook export cannot be inventoried safely."""


class FacebookMember(BaseModel):
    model_config = ConfigDict(frozen=True)

    member_id: str
    outer_archive_filename: str
    outer_archive_path: str = Field(exclude=True)
    member_path: str
    entry_index: int = Field(ge=0)
    filename: str
    extension: str
    product: str
    product_key: str
    uncompressed_size: int = Field(ge=0)
    compressed_size: int | None = Field(default=None, ge=0)
    zip_crc: int | None = Field(default=None, ge=0)
    is_directory: bool
    candidate_adapter: str | None
    accounting_category: Literal["classified", "structural", "unknown"]
    reason: str | None = None

    @property
    def source_locator(self) -> str:
        return "facebook-zip:" + json.dumps(
            {"archive": self.outer_archive_path, "entry_index": self.entry_index},
            ensure_ascii=True,
            separators=(",", ":"),
        )

    @property
    def is_leaf(self) -> bool:
        return not self.is_directory

    @property
    def thread_key(self) -> str | None:
        """Deterministic conversation identity shared by a thread's JSON and media."""

        parts = self.member_path.split("/")
        if (
            len(parts) >= 4
            and parts[0] == "your_facebook_activity"
            and parts[1] == "messages"
            and parts[2] in THREAD_CONTAINERS
        ):
            return f"{parts[2]}/{parts[3]}"
        return None


class FacebookProductInventory(BaseModel):
    model_config = ConfigDict(frozen=True)

    product: str
    product_key: str
    data_members: int
    logical_bytes: int
    adapters: tuple[str, ...]
    formats: tuple[str, ...]


class FacebookInventory(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_path: str
    outer_archives: int
    physical_source_bytes: int
    recursive_entries: int
    classified_data_members: int
    structural_members: int
    unknown_members_count: int
    logical_leaf_bytes: int
    products: tuple[FacebookProductInventory, ...]
    members: tuple[FacebookMember, ...]
    unknown_members: tuple[FacebookMember, ...]

    @model_validator(mode="after")
    def require_complete_accounting(self) -> FacebookInventory:
        if (
            self.classified_data_members + self.structural_members + self.unknown_members_count
            != self.recursive_entries
        ):
            raise ValueError("Facebook inventory does not account for every entry")
        if sum(item.uncompressed_size for item in self.members if item.is_leaf) != (
            self.logical_leaf_bytes
        ):
            raise ValueError("Facebook logical leaf-byte accounting is incomplete")
        return self

    @property
    def accounted_members(self) -> int:
        return self.classified_data_members + self.structural_members + self.unknown_members_count

    def public_dict(self, *, include_paths: bool = False) -> dict[str, object]:
        excluded = {"members", "unknown_members"}
        if not include_paths:
            excluded.add("source_path")
        data = self.model_dump(mode="json", exclude=excluded)
        fields = {
            "member_id",
            "product",
            "product_key",
            "extension",
            "uncompressed_size",
            "candidate_adapter",
            "accounting_category",
            "reason",
        }
        if include_paths:
            fields |= {"outer_archive_filename", "member_path", "filename"}
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


def _adapter_for(path: str, extension: str) -> str | None:
    if extension in ASSET_EXTENSIONS:
        return "facebook_asset"
    if extension == ".json":
        return "facebook_messages" if _MESSAGE_JSON.match(path) else "facebook_structured"
    return None


def _member(
    *, outer_path: Path, info: ZipInfo, entry_index: int
) -> FacebookMember:
    path = info.filename.replace("\\", "/")
    extension = Path(PurePosixPath(path).name).suffix.casefold()
    product = product_for(path)
    safe = _safe_path(path)
    adapter = None if info.is_dir() else _adapter_for(path, extension)
    if not safe:
        category: Literal["classified", "structural", "unknown"] = "unknown"
        reason = "unsafe_archive_path"
    elif info.is_dir():
        category = "structural"
        reason = "directory"
    elif product is None:
        category = "unknown"
        reason = "unregistered_product_path"
    elif extension not in SUPPORTED_EXTENSIONS or adapter is None:
        category = "unknown"
        reason = "unsupported_leaf_format"
    else:
        category = "classified"
        reason = None
    identity = f"{outer_path.name}\x00{path}#{entry_index}".encode()
    return FacebookMember(
        member_id=hashlib.sha256(identity).hexdigest()[:20],
        outer_archive_filename=outer_path.name,
        outer_archive_path=str(outer_path),
        member_path=path,
        entry_index=entry_index,
        filename=PurePosixPath(path).name,
        extension=extension,
        product=product.display_name if product else "",
        product_key=product.key if product else "",
        uncompressed_size=info.file_size,
        compressed_size=info.compress_size,
        zip_crc=info.CRC,
        is_directory=info.is_dir(),
        candidate_adapter=adapter,
        accounting_category=category,
        reason=reason,
    )


def _product_summaries(members: tuple[FacebookMember, ...]) -> tuple[FacebookProductInventory, ...]:
    grouped: dict[str, list[FacebookMember]] = defaultdict(list)
    for member in members:
        if member.accounting_category == "classified":
            grouped[member.product_key].append(member)
    return tuple(
        FacebookProductInventory(
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


def inspect_facebook(source_path: str | Path) -> FacebookInventory:
    """Inventory an official Facebook "Download Your Information" export ZIP."""

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
        raise FacebookInventoryError(
            "inspect-facebook requires a Facebook export ZIP or directory of ZIPs"
        )
    members: list[FacebookMember] = []
    for archive_path in archives:
        try:
            with ZipFile(archive_path) as archive:
                for entry_index, info in enumerate(archive.infolist()):
                    members.append(_member(outer_path=archive_path, info=info, entry_index=entry_index))
        except (BadZipFile, OSError) as error:
            raise FacebookInventoryError(
                f"invalid or unreadable ZIP archive: {archive_path}"
            ) from error
    all_members = tuple(members)
    unknown = tuple(item for item in all_members if item.accounting_category == "unknown")
    leaves = tuple(item for item in all_members if item.is_leaf)
    return FacebookInventory(
        source_path=str(path),
        outer_archives=len(archives),
        physical_source_bytes=sum(item.stat().st_size for item in archives),
        recursive_entries=len(all_members),
        classified_data_members=sum(
            item.accounting_category == "classified" for item in all_members
        ),
        structural_members=sum(item.accounting_category == "structural" for item in all_members),
        unknown_members_count=len(unknown),
        logical_leaf_bytes=sum(item.uncompressed_size for item in leaves),
        products=_product_summaries(all_members),
        members=all_members,
        unknown_members=unknown,
    )


@contextmanager
def open_facebook_member(member: FacebookMember) -> Iterator[BinaryIO]:
    with open_facebook_locator(member.source_locator) as stream:
        yield stream


@contextmanager
def open_facebook_locator(locator: str) -> Iterator[BinaryIO]:
    """Reopen persisted provenance without an in-memory inventory."""

    try:
        if not locator.startswith("facebook-zip:"):
            raise ValueError
        value = json.loads(locator.removeprefix("facebook-zip:"))
        archive_path = value["archive"]
        entry_index = value["entry_index"]
        if not isinstance(archive_path, str) or type(entry_index) is not int or entry_index < 0:
            raise ValueError
    except (ValueError, KeyError, TypeError) as error:
        raise FacebookInventoryError("invalid Facebook source locator") from error
    with ZipFile(archive_path) as archive:
        try:
            info = archive.infolist()[entry_index]
        except IndexError as error:
            raise FacebookInventoryError(
                "Facebook source locator no longer matches archive"
            ) from error
        with archive.open(info) as stream:
            yield stream


def _human_size(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{value} B"
        amount /= 1024
    raise AssertionError("unreachable")


def render_inventory_table(inventory: FacebookInventory) -> str:
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
    lines = ["Facebook Export Inventory", ""]
    for index, row in enumerate(rows):
        lines.append("  ".join(value.ljust(widths[column]) for column, value in enumerate(row)))
        if index == 0:
            lines.append("  ".join("-" * width for width in widths))
    lines.extend(
        [
            "",
            f"Accounted entries: {inventory.accounted_members:,}/{inventory.recursive_entries:,}",
            f"Data files: {inventory.classified_data_members:,}",
            f"Physical source size: {_human_size(inventory.physical_source_bytes)}",
            f"Logical leaf size: {_human_size(inventory.logical_leaf_bytes)}",
        ]
    )
    return "\n".join(lines)
