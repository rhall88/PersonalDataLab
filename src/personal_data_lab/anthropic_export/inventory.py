from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal
from zipfile import BadZipFile, ZipFile

from pydantic import BaseModel, ConfigDict, Field, computed_field

from ..anthropic_importer import SUPPORTED_CATEGORIES, _timestamp


class AnthropicInventoryError(ValueError):
    """Raised when an Anthropic export cannot be accounted safely."""


class AnthropicMember(BaseModel):
    model_config = ConfigDict(frozen=True)

    member_id: str
    archive_id: str
    category: str
    archive_path: str = Field(exclude=True)
    member_path: str = Field(exclude=True)
    filename: str = Field(exclude=True)
    sha256: str
    size_bytes: int = Field(ge=0)
    accounting_category: Literal["structured_json", "binary_asset"]

    def public_dict(self, *, include_paths: bool = False) -> dict[str, object]:
        result = self.model_dump(mode="json")
        if include_paths:
            result.update(
                archive_path=self.archive_path,
                member_path=self.member_path,
                filename=self.filename,
            )
        return result


class AnthropicArchive(BaseModel):
    model_config = ConfigDict(frozen=True)

    archive_id: str
    category: str
    source_path: str = Field(exclude=True)
    filename: str = Field(exclude=True)
    sha256: str
    size_bytes: int = Field(ge=0)
    members: tuple[AnthropicMember, ...]

    @computed_field
    @property
    def members_total(self) -> int:
        return len(self.members)

    @computed_field
    @property
    def structured_json_members(self) -> int:
        return sum(item.accounting_category == "structured_json" for item in self.members)

    @computed_field
    @property
    def binary_asset_members(self) -> int:
        return sum(item.accounting_category == "binary_asset" for item in self.members)

    def public_dict(self, *, include_paths: bool = False) -> dict[str, object]:
        result = self.model_dump(mode="json", exclude={"members"})
        if include_paths:
            result.update(source_path=self.source_path, filename=self.filename)
        result["members"] = [item.public_dict(include_paths=include_paths) for item in self.members]
        return result


class AnthropicInventory(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest_path: str = Field(exclude=True)
    manifest_id: str
    manifest_sha256: str
    manifest_size_bytes: int = Field(ge=0)
    exported_at: datetime | None = None
    export_id: str | None = None
    source_files_total: int
    declared_archives: int
    undeclared_archives: int
    undeclared_archive_ids: tuple[str, ...]
    physical_source_bytes: int
    archive_members_total: int
    structured_json_members: int
    binary_asset_members: int
    archives: tuple[AnthropicArchive, ...]

    @computed_field
    @property
    def unknown_files(self) -> int:
        return self.undeclared_archives

    @computed_field
    @property
    def complete_accounting(self) -> bool:
        return bool(
            self.source_files_total == 1 + self.declared_archives + self.undeclared_archives
            and self.archive_members_total
            == self.structured_json_members + self.binary_asset_members
            and self.declared_archives == len(self.archives)
            and self.archive_members_total
            == sum(archive.members_total for archive in self.archives)
        )

    def public_dict(self, *, include_paths: bool = False) -> dict[str, object]:
        result = self.model_dump(mode="json", exclude={"archives"})
        if include_paths:
            result["manifest_path"] = self.manifest_path
        result["archives"] = [
            item.public_dict(include_paths=include_paths) for item in self.archives
        ]
        return result


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _opaque_id(kind: str, *values: str) -> str:
    digest = hashlib.sha256("\x1f".join((kind, *values)).encode()).hexdigest()
    return f"anthropic-{kind}-{digest[:16]}"


def _required_string(record: dict[str, object], key: str, context: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise AnthropicInventoryError(f"{context} has no {key}")
    return value


def _safe_archive_filename(value: str) -> str:
    if Path(value).name != value or Path(value).suffix.casefold() != ".zip":
        raise AnthropicInventoryError("manifest contains an unsafe archive filename")
    return value


def _safe_member_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or path.is_absolute()
        or ".." in path.parts
        or any(part.endswith(":") for part in path.parts)
        or "\x00" in normalized
    ):
        raise AnthropicInventoryError("archive contains an unsafe member path")
    return path.as_posix()


def _archive_inventory(path: Path, *, category: str) -> AnthropicArchive:
    archive_sha256 = _sha256_file(path)
    archive_id = _opaque_id("archive", category, archive_sha256)
    members: list[AnthropicMember] = []
    seen: set[str] = set()
    try:
        with ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                member_path = _safe_member_path(info.filename)
                if member_path in seen:
                    raise AnthropicInventoryError("archive repeats a member path")
                seen.add(member_path)
                if info.flag_bits & 0x1:
                    raise AnthropicInventoryError("encrypted archive members are unsupported")
                raw = archive.read(info)
                accounting_category = (
                    "structured_json"
                    if PurePosixPath(member_path).suffix.casefold() == ".json"
                    else "binary_asset"
                )
                members.append(
                    AnthropicMember(
                        member_id=_opaque_id("member", category, archive_sha256, member_path),
                        archive_id=archive_id,
                        category=category,
                        archive_path=str(path),
                        member_path=member_path,
                        filename=PurePosixPath(member_path).name,
                        sha256=_sha256_bytes(raw),
                        size_bytes=info.file_size,
                        accounting_category=accounting_category,
                    )
                )
    except BadZipFile as exc:
        raise AnthropicInventoryError("manifest names an invalid ZIP archive") from exc
    if not members:
        raise AnthropicInventoryError("manifest names an empty ZIP archive")
    if not any(item.accounting_category == "structured_json" for item in members):
        raise AnthropicInventoryError("Anthropic archive contains no structured JSON member")
    return AnthropicArchive(
        archive_id=archive_id,
        category=category,
        source_path=str(path),
        filename=path.name,
        sha256=archive_sha256,
        size_bytes=path.stat().st_size,
        members=tuple(members),
    )


def inspect_anthropic(manifest_path: str | Path) -> AnthropicInventory:
    """Inventory an official manifest and every local archive it declares."""

    try:
        path = Path(manifest_path).expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise AnthropicInventoryError("Anthropic manifest does not exist") from exc
    if not path.is_file() or path.suffix.casefold() != ".json":
        raise AnthropicInventoryError("source must be an Anthropic export manifest JSON file")
    raw_manifest = path.read_bytes()
    try:
        payload = json.loads(raw_manifest)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnthropicInventoryError("Anthropic export manifest is invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data_files"), list):
        raise AnthropicInventoryError("Anthropic manifest data_files must be an array")

    root = path.parent.resolve()
    filenames: set[str] = set()
    archives: list[AnthropicArchive] = []
    for index, item in enumerate(payload["data_files"]):
        if not isinstance(item, dict):
            raise AnthropicInventoryError(f"manifest data file at index {index} must be an object")
        category = _required_string(item, "category", f"manifest data file at index {index}")
        if category not in SUPPORTED_CATEGORIES:
            raise AnthropicInventoryError("manifest contains an unsupported category")
        filename = _safe_archive_filename(
            _required_string(item, "filename", f"manifest data file at index {index}")
        )
        if filename in filenames:
            raise AnthropicInventoryError("manifest repeats an archive filename")
        filenames.add(filename)
        try:
            archive_path = (root / filename).resolve(strict=True)
        except FileNotFoundError as exc:
            raise AnthropicInventoryError("a manifest-declared archive is missing") from exc
        if archive_path.parent != root or not archive_path.is_file():
            raise AnthropicInventoryError("manifest archive is not a sibling file")
        archives.append(_archive_inventory(archive_path, category=category))

    if not archives:
        raise AnthropicInventoryError("Anthropic manifest declares no archives")
    undeclared_paths = sorted(
        item for item in root.glob("*.zip") if item.is_file() and item.name not in filenames
    )
    undeclared_ids = tuple(
        _opaque_id("undeclared", _sha256_file(item)) for item in undeclared_paths
    )
    members = [member for archive in archives for member in archive.members]
    return AnthropicInventory(
        manifest_path=str(path),
        manifest_id=_opaque_id("manifest", _sha256_bytes(raw_manifest)),
        manifest_sha256=_sha256_bytes(raw_manifest),
        manifest_size_bytes=len(raw_manifest),
        exported_at=_timestamp(payload.get("created_at")),
        export_id=str(payload["uuid"]) if payload.get("uuid") is not None else None,
        source_files_total=1 + len(archives) + len(undeclared_paths),
        declared_archives=len(archives),
        undeclared_archives=len(undeclared_paths),
        undeclared_archive_ids=undeclared_ids,
        physical_source_bytes=(
            len(raw_manifest)
            + sum(item.size_bytes for item in archives)
            + sum(item.stat().st_size for item in undeclared_paths)
        ),
        archive_members_total=len(members),
        structured_json_members=sum(
            item.accounting_category == "structured_json" for item in members
        ),
        binary_asset_members=sum(item.accounting_category == "binary_asset" for item in members),
        archives=tuple(archives),
    )


def render_inventory_table(inventory: AnthropicInventory) -> str:
    rows = [
        "Anthropic account-export inventory",
        f"Source files: {inventory.source_files_total}",
        f"Declared archives: {inventory.declared_archives}",
        f"Undeclared archives: {inventory.undeclared_archives}",
        f"Archive members: {inventory.archive_members_total}",
        f"Structured JSON members: {inventory.structured_json_members}",
        f"Binary asset members: {inventory.binary_asset_members}",
        f"Physical source bytes: {inventory.physical_source_bytes}",
    ]
    for archive in inventory.archives:
        rows.append(
            f"{archive.archive_id}  {archive.category}  "
            f"bytes={archive.size_bytes} members={archive.members_total}"
        )
    for archive_id in inventory.undeclared_archive_ids:
        rows.append(f"{archive_id}  undeclared_archive")
    return "\n".join(rows)
