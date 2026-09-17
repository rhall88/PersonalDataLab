from __future__ import annotations

from collections.abc import Callable
from time import perf_counter

from pydantic import BaseModel, ConfigDict, computed_field
from sqlalchemy.engine import Engine

from ..database import import_parsed_exports
from ..models import ImportResult, ParsedExport
from .inventory import IMessageSourceFile, inspect_imessages
from .parser import (
    ATTACHMENT_NAMESPACE,
    MESSAGE_NAMESPACE,
    PARTICIPANT_NAMESPACE,
    IMessageAdapterError,
    iter_imessage_exports,
)


class IMessageFileCoverage(BaseModel):
    model_config = ConfigDict(frozen=True)

    member_id: str
    records_discovered: int
    records_processed: int = 0
    chunks: int = 0
    short_circuited_chunks: int = 0
    conversations: int = 0
    participants: int = 0
    messages: int = 0
    reactions: int = 0
    edits: int = 0
    deletions: int = 0
    reply_relationships: int = 0
    resolved_replies: int = 0
    preserved_reply_references: int = 0
    attachment_relationships: int = 0
    locally_available_attachments: int = 0
    conflicts: int = 0
    parse_warnings: int = 0
    failures: int = 0
    elapsed_seconds: float = 0.0


class IMessageImportSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_files_total: int
    csv_files: int
    attachment_files: int
    unknown_files: int
    physical_source_bytes: int
    records_discovered: int
    reactions_discovered: int
    edits_discovered: int
    deletions_discovered: int
    replies_discovered: int
    attachment_references_discovered: int
    files_processed: int
    files_short_circuited: int
    files_failed: int
    chunks: int
    conversations: int
    participants: int
    messages: int
    reactions: int
    edits: int
    deletions: int
    reply_relationships: int
    resolved_replies: int
    preserved_reply_references: int
    attachment_relationships: int
    locally_available_attachments: int
    conflicts: int
    parse_warnings: int
    coverage: dict[str, IMessageFileCoverage]

    @computed_field
    @property
    def complete_accounting(self) -> bool:
        return bool(
            self.source_files_total == self.csv_files + self.attachment_files + self.unknown_files
            and self.files_processed + self.files_failed == self.csv_files
            and self.messages == self.records_discovered
            and self.reactions == self.reactions_discovered
            and self.edits == self.edits_discovered
            and self.deletions == self.deletions_discovered
            and self.reply_relationships == self.replies_discovered
            and self.attachment_relationships == self.attachment_references_discovered
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
            and self.reply_relationships == self.resolved_replies + self.preserved_reply_references
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


MemberCallback = Callable[[IMessageSourceFile], None]
ImportedCallback = Callable[[IMessageSourceFile, ParsedExport, ImportResult], None]


def _conflicts(result: ImportResult) -> int:
    return (
        result.conflicted_conversations
        + result.conflicted_events
        + result.conflicted_records
        + result.conflicted_assets
        + result.conflicted_relationships
    )


def _chunk_counts(parsed: ParsedExport) -> dict[str, int | set[str]]:
    events = [event for conversation in parsed.conversations for event in conversation.events]
    reply_relationships = [
        item for item in parsed.relationships if item.relationship_type == "message_replies_to"
    ]
    attachment_relationships = [
        item for item in parsed.relationships if item.relationship_type == "message_has_attachment"
    ]
    return {
        "conversation_ids": {item.source_conversation_id for item in parsed.conversations},
        "participant_ids": {
            item.source_record_id
            for item in parsed.records
            if item.source_record_namespace == PARTICIPANT_NAMESPACE
        },
        "messages": sum(item.source_record_namespace == MESSAGE_NAMESPACE for item in events),
        "reactions": sum(
            item.event_type in {"message.reaction", "message.reaction_removed"} for item in events
        ),
        "edits": sum(item.event_type == "message.edit" for item in events),
        "deletions": sum(item.event_type == "message.deletion" for item in events),
        "reply_relationships": len(reply_relationships),
        "resolved_replies": sum(item.object_kind == "event" for item in reply_relationships),
        "preserved_reply_references": sum(
            item.object_kind == "record" for item in reply_relationships
        ),
        "attachment_relationships": len(attachment_relationships),
        "locally_available_attachments": sum(
            bool(item.metadata.get("imazing", {}).get("available_locally"))
            for item in parsed.assets
            if item.asset_namespace == ATTACHMENT_NAMESPACE
        ),
    }


def import_imessages_all(
    engine: Engine,
    source_path: str,
    *,
    timezone_name: str,
    device_label: str | None = None,
    on_member_start: MemberCallback | None = None,
    on_member_imported: ImportedCallback | None = None,
) -> IMessageImportSummary:
    """Inventory and import all retained iMazing CSVs in restart-safe chunks."""

    inventory = inspect_imessages(source_path)
    coverage: dict[str, IMessageFileCoverage] = {}
    conversation_ids: set[str] = set()
    participant_ids: set[str] = set()
    totals = {
        "files_processed": 0,
        "files_short_circuited": 0,
        "files_failed": 0,
        "chunks": 0,
        "messages": 0,
        "reactions": 0,
        "edits": 0,
        "deletions": 0,
        "reply_relationships": 0,
        "resolved_replies": 0,
        "preserved_reply_references": 0,
        "attachment_relationships": 0,
        "locally_available_attachments": 0,
        "conflicts": 0,
        "parse_warnings": 0,
    }
    for member in inventory.files:
        if member.accounting_category != "classified_csv":
            continue
        started = perf_counter()
        mutable: dict[str, object] = {
            "member_id": member.member_id,
            "records_discovered": member.records,
            "records_processed": 0,
            "chunks": 0,
            "short_circuited_chunks": 0,
            "conversation_ids": set(),
            "participant_ids": set(),
            "messages": 0,
            "reactions": 0,
            "edits": 0,
            "deletions": 0,
            "reply_relationships": 0,
            "resolved_replies": 0,
            "preserved_reply_references": 0,
            "attachment_relationships": 0,
            "locally_available_attachments": 0,
            "conflicts": 0,
            "parse_warnings": 0,
            "failures": 0,
        }
        if on_member_start is not None:
            on_member_start(member)
        try:
            for parsed in iter_imessage_exports(
                member,
                inventory,
                timezone_name=timezone_name,
                device_label=device_label,
            ):
                result = import_parsed_exports(engine, (parsed,))[0]
                counts = _chunk_counts(parsed)
                mutable["chunks"] = int(mutable["chunks"]) + 1
                mutable["short_circuited_chunks"] = int(mutable["short_circuited_chunks"]) + int(
                    result.short_circuited
                )
                mutable["parse_warnings"] = int(mutable["parse_warnings"]) + len(
                    parsed.parse_warnings
                )
                mutable["conflicts"] = int(mutable["conflicts"]) + _conflicts(result)
                mutable["conversation_ids"].update(counts["conversation_ids"])
                mutable["participant_ids"].update(counts["participant_ids"])
                for name in (
                    "messages",
                    "reactions",
                    "edits",
                    "deletions",
                    "reply_relationships",
                    "resolved_replies",
                    "preserved_reply_references",
                    "attachment_relationships",
                    "locally_available_attachments",
                ):
                    mutable[name] = int(mutable[name]) + int(counts[name])
                if on_member_imported is not None:
                    on_member_imported(member, parsed, result)
            if not mutable["chunks"]:
                raise IMessageAdapterError("message adapter produced no parsed chunks")
            mutable["records_processed"] = mutable["messages"]
            totals["files_processed"] += 1
            if mutable["short_circuited_chunks"] == mutable["chunks"]:
                totals["files_short_circuited"] += 1
        except Exception:  # noqa: BLE001 - isolate failures to opaque member IDs
            mutable["failures"] = 1
            totals["files_failed"] += 1
        elapsed = perf_counter() - started
        member_conversations = set(mutable.pop("conversation_ids"))
        member_participants = set(mutable.pop("participant_ids"))
        conversation_ids.update(member_conversations)
        participant_ids.update(member_participants)
        mutable["conversations"] = len(member_conversations)
        mutable["participants"] = len(member_participants)
        mutable["elapsed_seconds"] = elapsed
        item = IMessageFileCoverage.model_validate(mutable)
        coverage[member.member_id] = item
        totals["chunks"] += item.chunks
        for name in (
            "messages",
            "reactions",
            "edits",
            "deletions",
            "reply_relationships",
            "resolved_replies",
            "preserved_reply_references",
            "attachment_relationships",
            "locally_available_attachments",
            "conflicts",
            "parse_warnings",
        ):
            totals[name] += int(getattr(item, name))
    return IMessageImportSummary(
        source_files_total=inventory.source_files_total,
        csv_files=inventory.csv_files,
        attachment_files=inventory.attachment_files,
        unknown_files=inventory.unknown_files,
        physical_source_bytes=inventory.physical_source_bytes,
        records_discovered=inventory.records,
        reactions_discovered=inventory.reactions,
        edits_discovered=inventory.edits,
        deletions_discovered=inventory.deletions,
        replies_discovered=inventory.replies,
        attachment_references_discovered=inventory.attachment_references,
        files_processed=totals["files_processed"],
        files_short_circuited=totals["files_short_circuited"],
        files_failed=totals["files_failed"],
        chunks=totals["chunks"],
        conversations=len(conversation_ids),
        participants=len(participant_ids),
        messages=totals["messages"],
        reactions=totals["reactions"],
        edits=totals["edits"],
        deletions=totals["deletions"],
        reply_relationships=totals["reply_relationships"],
        resolved_replies=totals["resolved_replies"],
        preserved_reply_references=totals["preserved_reply_references"],
        attachment_relationships=totals["attachment_relationships"],
        locally_available_attachments=totals["locally_available_attachments"],
        conflicts=totals["conflicts"],
        parse_warnings=totals["parse_warnings"],
        coverage=coverage,
    )
