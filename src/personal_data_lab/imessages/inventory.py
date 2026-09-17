from __future__ import annotations

import csv
import hashlib
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

EXPECTED_HEADERS = (
    "Chat Session",
    "Message Date",
    "Delivered Date",
    "Read Date",
    "Edited Date",
    "Deleted Date",
    "Service",
    "Type",
    "Sender ID",
    "Sender Name",
    "Status",
    "Replying to",
    "Subject",
    "Text",
    "Reactions",
    "Attachment",
    "Attachment type",
)
SUPPORTED_ASSET_EXTENSIONS = {
    ".3gp",
    ".aac",
    ".caf",
    ".csv",
    ".gif",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".json",
    ".m4a",
    ".m4v",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".tif",
    ".tiff",
    ".txt",
    ".vcf",
    ".wav",
    ".webp",
}


class IMessageInventoryError(ValueError):
    """Raised when a device-message source cannot be inventoried safely."""


class IMessageSourceFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    member_id: str
    source_path: str = Field(exclude=True)
    relative_path: str = Field(exclude=True)
    filename: str = Field(exclude=True)
    sha256: str
    size_bytes: int
    accounting_category: Literal["classified_csv", "attachment", "unknown"]
    candidate_adapter: str | None = None
    reason: str | None = None
    delimiter: Literal["comma", "semicolon", "tab"] | None = None
    records: int = 0
    chat_sessions: int = 0
    replies: int = 0
    reactions: int = 0
    edits: int = 0
    deletions: int = 0
    attachment_references: int = 0
    message_types: dict[str, int] = Field(default_factory=dict)
    services: dict[str, int] = Field(default_factory=dict)

    def public_dict(self, *, include_paths: bool = False) -> dict[str, object]:
        result = self.model_dump(mode="json")
        if include_paths:
            result["relative_path"] = self.relative_path
            result["filename"] = self.filename
        return result


class IMessageInventory(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_files_total: int
    csv_files: int
    attachment_files: int
    unknown_files: int
    physical_source_bytes: int
    csv_bytes: int
    attachment_bytes: int
    records: int
    chat_sessions: int
    replies: int
    reactions: int
    edits: int
    deletions: int
    attachment_references: int
    files: tuple[IMessageSourceFile, ...]

    @computed_field
    @property
    def accounted_files(self) -> int:
        return self.csv_files + self.attachment_files + self.unknown_files

    @computed_field
    @property
    def complete_accounting(self) -> bool:
        return self.source_files_total == self.accounted_files

    def public_dict(self, *, include_paths: bool = False) -> dict[str, object]:
        result = self.model_dump(mode="json", exclude={"files"})
        result["files"] = [item.public_dict(include_paths=include_paths) for item in self.files]
        return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _member_id(relative_path: str, sha256: str) -> str:
    digest = hashlib.sha256(f"{relative_path}\x1f{sha256}".encode()).hexdigest()
    return f"imsg-{digest[:16]}"


def _dialect(sample: str) -> tuple[type[csv.Dialect], str]:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error as exc:
        raise IMessageInventoryError("message CSV delimiter could not be identified") from exc
    names = {",": "comma", ";": "semicolon", "\t": "tab"}
    if dialect.delimiter not in names:
        raise IMessageInventoryError("message CSV uses an unsupported delimiter")
    return dialect, names[dialect.delimiter]


def _inspect_csv(path: Path, relative_path: str, sha256: str) -> IMessageSourceFile:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            sample = handle.read(64 * 1024)
            handle.seek(0)
            dialect, delimiter = _dialect(sample)
            reader = csv.DictReader(handle, dialect=dialect)
            if tuple(reader.fieldnames or ()) != EXPECTED_HEADERS:
                return IMessageSourceFile(
                    member_id=_member_id(relative_path, sha256),
                    source_path=str(path),
                    relative_path=relative_path,
                    filename=path.name,
                    sha256=sha256,
                    size_bytes=path.stat().st_size,
                    accounting_category="unknown",
                    reason="unrecognized_csv_schema",
                )
            records = 0
            sessions: set[str] = set()
            counts: Counter[str] = Counter()
            message_types: Counter[str] = Counter()
            services: Counter[str] = Counter()
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    return IMessageSourceFile(
                        member_id=_member_id(relative_path, sha256),
                        source_path=str(path),
                        relative_path=relative_path,
                        filename=path.name,
                        sha256=sha256,
                        size_bytes=path.stat().st_size,
                        accounting_category="unknown",
                        reason="malformed_csv_row",
                    )
                records += 1
                sessions.add(row["Chat Session"])
                message_types[row["Type"] or "unspecified"] += 1
                services[row["Service"] or "unspecified"] += 1
                counts["replies"] += bool(row["Replying to"].strip())
                counts["reactions"] += sum(
                    1 for item in row["Reactions"].splitlines() if item.strip()
                )
                counts["edits"] += bool(row["Edited Date"].strip())
                counts["deletions"] += bool(
                    row["Deleted Date"].strip()
                    or row["Status"].strip().casefold() == "recently deleted"
                )
                counts["attachments"] += bool(row["Attachment"].strip())
    except (OSError, UnicodeError, csv.Error) as exc:
        raise IMessageInventoryError("message CSV could not be read safely") from exc
    return IMessageSourceFile(
        member_id=_member_id(relative_path, sha256),
        source_path=str(path),
        relative_path=relative_path,
        filename=path.name,
        sha256=sha256,
        size_bytes=path.stat().st_size,
        accounting_category="classified_csv",
        candidate_adapter="imazing_csv",
        delimiter=delimiter,
        records=records,
        chat_sessions=len(sessions),
        replies=counts["replies"],
        reactions=counts["reactions"],
        edits=counts["edits"],
        deletions=counts["deletions"],
        attachment_references=counts["attachments"],
        message_types=dict(sorted(message_types.items())),
        services=dict(sorted(services.items())),
    )


def inspect_imessages(source_path: str | Path) -> IMessageInventory:
    """Inventory an iMazing Messages CSV export without modifying its source files."""

    source = Path(source_path).expanduser()
    if not source.exists():
        raise IMessageInventoryError("message-export source does not exist")
    if source.is_file():
        paths = [source]
        root = source.parent
    elif source.is_dir():
        paths = sorted(path for path in source.rglob("*") if path.is_file())
        root = source
    else:
        raise IMessageInventoryError("message-export source must be a file or directory")
    if not paths:
        raise IMessageInventoryError("message-export source contains no files")

    files: list[IMessageSourceFile] = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        sha256 = _sha256(path)
        if path.suffix.casefold() == ".csv":
            files.append(_inspect_csv(path, relative, sha256))
            continue
        category: Literal["attachment", "unknown"]
        reason = None
        if path.suffix.casefold() in SUPPORTED_ASSET_EXTENSIONS:
            category = "attachment"
        else:
            category = "unknown"
            reason = "unsupported_file_type"
        files.append(
            IMessageSourceFile(
                member_id=_member_id(relative, sha256),
                source_path=str(path),
                relative_path=relative,
                filename=path.name,
                sha256=sha256,
                size_bytes=path.stat().st_size,
                accounting_category=category,
                reason=reason,
            )
        )

    csv_files = [item for item in files if item.accounting_category == "classified_csv"]
    attachments = [item for item in files if item.accounting_category == "attachment"]
    unknown = [item for item in files if item.accounting_category == "unknown"]
    return IMessageInventory(
        source_files_total=len(files),
        csv_files=len(csv_files),
        attachment_files=len(attachments),
        unknown_files=len(unknown),
        physical_source_bytes=sum(item.size_bytes for item in files),
        csv_bytes=sum(item.size_bytes for item in csv_files),
        attachment_bytes=sum(item.size_bytes for item in attachments),
        records=sum(item.records for item in csv_files),
        chat_sessions=sum(item.chat_sessions for item in csv_files),
        replies=sum(item.replies for item in csv_files),
        reactions=sum(item.reactions for item in csv_files),
        edits=sum(item.edits for item in csv_files),
        deletions=sum(item.deletions for item in csv_files),
        attachment_references=sum(item.attachment_references for item in csv_files),
        files=tuple(files),
    )


def render_inventory_table(inventory: IMessageInventory) -> str:
    rows = [
        "iMessages device-export inventory",
        f"Files: {inventory.source_files_total}",
        f"CSV files: {inventory.csv_files}",
        f"Attachment files: {inventory.attachment_files}",
        f"Unknown files: {inventory.unknown_files}",
        f"Physical source bytes: {inventory.physical_source_bytes}",
        f"Messages: {inventory.records}",
        f"Chat sessions: {inventory.chat_sessions}",
        f"Reply references: {inventory.replies}",
        f"Rows with reactions: {inventory.reactions}",
        f"Attachment references: {inventory.attachment_references}",
    ]
    for item in inventory.files:
        rows.append(
            f"{item.member_id}  {item.accounting_category}  "
            f"bytes={item.size_bytes} records={item.records}"
        )
    return "\n".join(rows)
