from __future__ import annotations

import hashlib
import json
from collections import Counter
from contextlib import AbstractContextManager
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal, Self
from zipfile import BadZipFile, ZipFile

from pydantic import BaseModel, ConfigDict, Field, computed_field

MANIFEST_NAME = "export_manifest.json"


class OpenAIInventoryError(ValueError):
    """Raised when an official OpenAI export cannot be accounted safely."""


class OpenAIPhysicalMember(BaseModel):
    model_config = ConfigDict(frozen=True)

    member_id: str
    member_path: str = Field(exclude=True)
    filename: str = Field(exclude=True)
    logical_path: str = Field(exclude=True)
    size_bytes: int
    accounting_category: Literal[
        "manifest",
        "conversation_shard",
        "structured_json",
        "binary_asset",
        "rendered_html",
        "unknown",
    ]

    def public_dict(self, *, include_paths: bool = False) -> dict[str, object]:
        result = self.model_dump(mode="json")
        if include_paths:
            result.update(
                member_path=self.member_path,
                filename=self.filename,
                logical_path=self.logical_path,
            )
        return result


class OpenAILogicalFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    member_id: str
    logical_path: str = Field(exclude=True)
    physical_paths: tuple[str, ...] = Field(exclude=True)
    size_bytes: int
    shards: int
    sharded: bool
    accounting_category: Literal[
        "conversations", "structured_json", "binary_asset", "rendered_html", "unknown"
    ]

    def public_dict(self, *, include_paths: bool = False) -> dict[str, object]:
        result = self.model_dump(mode="json")
        if include_paths:
            result["logical_path"] = self.logical_path
            result["physical_paths"] = list(self.physical_paths)
        return result


class OpenAIInventory(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_path: str = Field(exclude=True)
    source_kind: Literal["zip", "directory"]
    manifest_version: int
    physical_members_total: int
    logical_files_total: int
    physical_source_bytes: int
    logical_source_bytes: int
    conversation_shards: int
    structured_json_files: int
    binary_asset_files: int
    rendered_html_files: int
    unknown_files: int
    members: tuple[OpenAIPhysicalMember, ...]
    logical_files: tuple[OpenAILogicalFile, ...]

    @computed_field
    @property
    def complete_accounting(self) -> bool:
        return bool(
            self.physical_members_total == len(self.members)
            and self.logical_files_total == len(self.logical_files)
            and self.physical_members_total
            == 1
            + self.conversation_shards
            + self.structured_json_files
            + self.binary_asset_files
            + self.rendered_html_files
            + self.unknown_files
        )

    def public_dict(self, *, include_paths: bool = False) -> dict[str, object]:
        result = self.model_dump(mode="json", exclude={"members", "logical_files"})
        result["members"] = [item.public_dict(include_paths=include_paths) for item in self.members]
        result["logical_files"] = [
            item.public_dict(include_paths=include_paths) for item in self.logical_files
        ]
        return result


class OpenAIExportReader(AbstractContextManager["OpenAIExportReader"]):
    """Open a ZIP or extracted official export while retaining source bytes read-only."""

    def __init__(self, source_path: str | Path):
        self.path = Path(source_path).expanduser().resolve(strict=True)
        self.archive: ZipFile | None = None
        if self.path.is_file() and self.path.suffix.casefold() != ".zip":
            raise OpenAIInventoryError("source must be an official OpenAI ZIP or extracted folder")
        if not self.path.is_file() and not self.path.is_dir():
            raise OpenAIInventoryError("source must be an official OpenAI ZIP or extracted folder")

    @property
    def source_kind(self) -> Literal["zip", "directory"]:
        return "zip" if self.path.is_file() else "directory"

    def __enter__(self) -> Self:
        if self.source_kind == "zip":
            try:
                self.archive = ZipFile(self.path)
            except BadZipFile as exc:
                raise OpenAIInventoryError(f"invalid ZIP archive: {self.path}") from exc
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self.archive is not None:
            self.archive.close()
            self.archive = None

    def names_and_sizes(self) -> dict[str, int]:
        if self.archive is not None:
            result: dict[str, int] = {}
            for info in self.archive.infolist():
                if info.is_dir():
                    continue
                name = _safe_member_path(info.filename)
                if name in result:
                    raise OpenAIInventoryError(f"duplicate ZIP member: {name}")
                if info.flag_bits & 0x1:
                    raise OpenAIInventoryError(f"encrypted ZIP member is unsupported: {name}")
                result[name] = info.file_size
            return result
        result = {}
        for path in sorted(item for item in self.path.rglob("*") if item.is_file()):
            name = path.relative_to(self.path).as_posix()
            result[_safe_member_path(name)] = path.stat().st_size
        return result

    def open_binary(self, member_path: str) -> BinaryIO:
        member_path = _safe_member_path(member_path)
        if self.archive is not None:
            return self.archive.open(member_path, "r")
        return (self.path / PurePosixPath(member_path)).open("rb")

    def read_bytes(self, member_path: str) -> bytes:
        with self.open_binary(member_path) as handle:
            return handle.read()

    def locator(self, member_path: str) -> str:
        member_path = _safe_member_path(member_path)
        if self.source_kind == "zip":
            return f"{self.path}!/{member_path}"
        return str((self.path / PurePosixPath(member_path)).resolve())


def _safe_member_path(value: str) -> str:
    normalized = value.replace("\\", "/").lstrip("/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise OpenAIInventoryError("export contains an unsafe member path")
    return path.as_posix()


def _member_id(kind: str, path: str) -> str:
    digest = hashlib.sha256(f"{kind}\x1f{path}".encode()).hexdigest()
    return f"openai-{digest[:16]}"


def _logical_category(path: str) -> str:
    name = PurePosixPath(path).name.casefold()
    if name == "conversations.json":
        return "conversations"
    if name.endswith((".dat", ".bundle")):
        return "binary_asset"
    if name.endswith(".html"):
        return "rendered_html"
    if name.endswith(".json"):
        return "structured_json"
    return "unknown"


def _physical_category(logical_category: str, *, manifest: bool = False) -> str:
    if manifest:
        return "manifest"
    if logical_category == "conversations":
        return "conversation_shard"
    return logical_category


def _manifest(reader: OpenAIExportReader) -> dict[str, object]:
    try:
        payload = json.loads(reader.read_bytes(MANIFEST_NAME))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenAIInventoryError("export_manifest.json is missing or invalid") from exc
    if not isinstance(payload, dict):
        raise OpenAIInventoryError("export_manifest.json must contain an object")
    if not isinstance(payload.get("version"), int):
        raise OpenAIInventoryError("export manifest has no integer version")
    if payload.get("manifest_file") != MANIFEST_NAME:
        raise OpenAIInventoryError("export manifest does not identify itself consistently")
    if not isinstance(payload.get("export_files"), list) or not isinstance(
        payload.get("logical_files"), dict
    ):
        raise OpenAIInventoryError("export manifest has an unsupported schema")
    return payload


def _inspect(reader: OpenAIExportReader) -> OpenAIInventory:
    actual = reader.names_and_sizes()
    payload = _manifest(reader)
    export_files = payload["export_files"]
    logical_payload = payload["logical_files"]
    assert isinstance(export_files, list)
    assert isinstance(logical_payload, dict)

    expected: dict[str, int] = {}
    for item in export_files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise OpenAIInventoryError("export manifest contains an invalid physical file")
        path = _safe_member_path(item["path"])
        size = item.get("size_bytes")
        if not isinstance(size, int) or size < 0:
            raise OpenAIInventoryError("export manifest contains an invalid file size")
        if path in expected:
            raise OpenAIInventoryError(f"export manifest repeats physical file {path}")
        expected[path] = size
    manifest_size = actual.get(MANIFEST_NAME)
    if manifest_size is None:
        raise OpenAIInventoryError("export manifest is not present in the retained source")
    expected_with_manifest = expected | {MANIFEST_NAME: manifest_size}
    if set(actual) != set(expected_with_manifest):
        missing = len(set(expected_with_manifest) - set(actual))
        unknown = len(set(actual) - set(expected_with_manifest))
        raise OpenAIInventoryError(
            f"manifest/source membership mismatch: {missing} missing, {unknown} unknown"
        )
    size_mismatches = [
        path for path, size in expected_with_manifest.items() if actual[path] != size
    ]
    if size_mismatches:
        raise OpenAIInventoryError(
            f"manifest/source size mismatch for {len(size_mismatches)} member(s)"
        )

    logical_files: list[OpenAILogicalFile] = []
    physical_to_logical: dict[str, str] = {}
    for raw_logical_path, raw_spec in logical_payload.items():
        if not isinstance(raw_logical_path, str) or not isinstance(raw_spec, dict):
            raise OpenAIInventoryError("export manifest contains an invalid logical file")
        logical_path = _safe_member_path(raw_logical_path)
        raw_files = raw_spec.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise OpenAIInventoryError(f"logical file has no physical members: {logical_path}")
        paths: list[str] = []
        for raw_path in raw_files:
            if not isinstance(raw_path, str):
                raise OpenAIInventoryError("logical file contains an invalid physical path")
            path = _safe_member_path(raw_path)
            if path not in expected:
                raise OpenAIInventoryError(f"logical file references an unknown member: {path}")
            if path in physical_to_logical:
                raise OpenAIInventoryError(
                    f"physical member belongs to multiple logical files: {path}"
                )
            physical_to_logical[path] = logical_path
            paths.append(path)
        sharded = raw_spec.get("sharded")
        if not isinstance(sharded, bool) or sharded != (len(paths) > 1):
            raise OpenAIInventoryError(f"logical sharding flag is inconsistent: {logical_path}")
        category = _logical_category(logical_path)
        logical_files.append(
            OpenAILogicalFile(
                member_id=_member_id("logical", logical_path),
                logical_path=logical_path,
                physical_paths=tuple(paths),
                size_bytes=sum(expected[path] for path in paths),
                shards=len(paths),
                sharded=sharded,
                accounting_category=category,
            )
        )
    if set(physical_to_logical) != set(expected):
        raise OpenAIInventoryError("not every physical export member belongs to a logical file")

    physical_members = [
        OpenAIPhysicalMember(
            member_id=_member_id("physical", MANIFEST_NAME),
            member_path=MANIFEST_NAME,
            filename=MANIFEST_NAME,
            logical_path=MANIFEST_NAME,
            size_bytes=manifest_size,
            accounting_category="manifest",
        )
    ]
    logical_by_path = {item.logical_path: item for item in logical_files}
    for path in sorted(expected):
        logical_path = physical_to_logical[path]
        logical = logical_by_path[logical_path]
        physical_members.append(
            OpenAIPhysicalMember(
                member_id=_member_id("physical", path),
                member_path=path,
                filename=PurePosixPath(path).name,
                logical_path=logical_path,
                size_bytes=expected[path],
                accounting_category=_physical_category(logical.accounting_category),
            )
        )
    counts = Counter(item.accounting_category for item in physical_members)
    return OpenAIInventory(
        source_path=str(reader.path),
        source_kind=reader.source_kind,
        manifest_version=int(payload["version"]),
        physical_members_total=len(physical_members),
        logical_files_total=len(logical_files),
        physical_source_bytes=sum(actual.values()),
        logical_source_bytes=sum(item.size_bytes for item in logical_files),
        conversation_shards=counts["conversation_shard"],
        structured_json_files=counts["structured_json"],
        binary_asset_files=counts["binary_asset"],
        rendered_html_files=counts["rendered_html"],
        unknown_files=counts["unknown"],
        members=tuple(physical_members),
        logical_files=tuple(sorted(logical_files, key=lambda item: item.logical_path)),
    )


def inspect_openai(source_path: str | Path) -> OpenAIInventory:
    with OpenAIExportReader(source_path) as reader:
        return _inspect(reader)
