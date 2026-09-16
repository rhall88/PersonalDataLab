from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import BinaryIO

from ..models import NormalizedEvent, NormalizedRecord, ParsedExport
from .adapters import GoogleAdapterError, MatchResult
from .inventory import TakeoutMember
from .products import SupportLevel
from .rich_assets import _asset
from .semantic import (
    SEMANTIC_MAX_MEMBER_BYTES,
    _authority,
    _decode_structured,
    _dedupe_identity,
    _first_string,
    _identity,
    _parsed_export,
    _read_bounded,
    _text_projection,
    _time,
)

LONG_TAIL_PRODUCTS = (
    "access_log_activity",
    "alerts",
    "arts_culture",
    "assignments",
    "blogger",
    "chrome",
    "classroom",
    "appeal_content",
    "discover",
    "google_ads",
    "business_profile",
    "google_developers",
    "google_earth",
    "google_feedback",
    "google_health",
    "help_communities",
    "play_games",
    "play_movies_tv",
    "product_surveys",
    "workspace_marketplace",
    "news",
    "search_contributions",
    "search_notifications",
    "tenor",
    "youtube",
)

STRUCTURED_EXTENSIONS = (
    ".atom",
    ".csv",
    ".geojson",
    ".htm",
    ".html",
    ".json",
    ".kml",
    ".tsv",
    ".txt",
    ".xml",
)

_EVENT_TIME_FIELDS = (
    "Activity Timestamp",
    "Comment Create Timestamp",
    "Message Create Timestamp (UTC)",
    "Liked Date",
    "Published",
    "submission_time",
    "promptTime",
    "createdAt",
    "creationTime",
    "timestamp",
    "time",
    "date",
)


class _HTMLStructure(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []
        self._cell: list[str] | None = None
        self._row: list[str] | None = None
        self.visible: list[str] = []
        self.links: list[dict[str, str]] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        folded = tag.casefold()
        if folded in {"script", "style"}:
            self._ignored_depth += 1
        if folded == "a":
            self._anchor_href = dict(attrs).get("href")
            self._anchor_text = []
        elif folded == "tr":
            self._row = []
        elif folded in {"td", "th"}:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        folded = tag.casefold()
        if folded in {"script", "style"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)
        if folded == "a" and self._anchor_href:
            self.links.append(
                {"url": self._anchor_href, "title": " ".join(self._anchor_text).strip()}
            )
            self._anchor_href = None
            self._anchor_text = []
        elif folded in {"td", "th"} and self._cell is not None:
            if self._row is not None:
                self._row.append(" ".join(self._cell).strip())
            self._cell = None
        elif folded == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(data.split())
        if not cleaned or self._ignored_depth:
            return
        self.visible.append(cleaned)
        if self._anchor_href is not None:
            self._anchor_text.append(cleaned)
        if self._cell is not None:
            self._cell.append(cleaned)


def _path_token(member: TakeoutMember) -> str:
    relative = member.product_relative_path or member.member_path
    return hashlib.sha256(PurePosixPath(relative).as_posix().encode()).hexdigest()[:16]


def _csv_values(content: bytes, extension: str) -> list[object]:
    text = content.decode("utf-8-sig", errors="strict")
    if not text.strip():
        return []
    if extension == ".tsv":
        dialect = csv.excel_tab
    else:
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel
    return [dict(row) for row in csv.DictReader(io.StringIO(text, newline=""), dialect=dialect)]


def _html_values(content: bytes, product_key: str) -> list[object]:
    text = content.decode("utf-8-sig", errors="strict")
    if not text.strip():
        return []
    parser = _HTMLStructure()
    parser.feed(text)
    if product_key == "chrome" and parser.links:
        return [{"dataset": "bookmarks", **link} for link in parser.links]
    if product_key == "play_games" and len(parser.rows) > 1:
        headers = parser.rows[0]
        return [
            {
                "dataset": "game_table",
                **{
                    (headers[index] if index < len(headers) and headers[index] else f"column_{index}"): cell
                    for index, cell in enumerate(row)
                },
            }
            for row in parser.rows[1:]
        ]
    value: dict[str, object] = {"visible_text": " ".join(parser.visible)}
    if parser.links:
        value["links"] = parser.links
    if parser.rows:
        value["table_rows"] = parser.rows
    return [value] if value["visible_text"] else []


def _extensionless_values(content: bytes) -> list[object]:
    text = content.decode("utf-8-sig", errors="strict")
    if not text.strip():
        return []
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return [{"raw_text": text}]
    return list(decoded) if isinstance(decoded, list) else [decoded]


def _decode(content: bytes, member: TakeoutMember) -> list[object]:
    extension = member.extension
    if extension in {".csv", ".tsv"}:
        return _csv_values(content, extension)
    if extension in {".html", ".htm"}:
        return _html_values(content, member.product_key or "")
    if extension == "":
        return _extensionless_values(content)
    if extension == ".geojson":
        return _decode_structured(content, ".json")
    if extension == ".atom":
        return _decode_structured(content, ".xml")
    return _decode_structured(content, extension)


def _expand_values(values: list[object], product_key: str) -> list[dict[str, object]]:
    expanded: list[dict[str, object]] = []
    for raw in values:
        if not isinstance(raw, dict):
            expanded.append({"value": raw})
            continue
        if not raw:
            continue
        if product_key in {"chrome", "google_developers"}:
            emitted = False
            scalars = {key: value for key, value in raw.items() if not isinstance(value, list)}
            for dataset, items in raw.items():
                if not isinstance(items, list):
                    continue
                emitted = True
                for item in items:
                    expanded.append(
                        {
                            "dataset": str(dataset),
                            "value": item,
                            **({"context": scalars} if scalars else {}),
                        }
                    )
            if scalars:
                expanded.append({"dataset": "settings", **scalars})
            elif not emitted:
                expanded.append(raw)
            continue
        if product_key == "tenor" and isinstance(raw.get("favorited_uploads"), list):
            context = {key: value for key, value in raw.items() if key != "favorited_uploads"}
            expanded.extend(
                {"dataset": "favorite", "value": item, "context": context}
                for item in raw["favorited_uploads"]
            )
            if context:
                expanded.append({"dataset": "pack", **context})
            continue
        if len(raw) == 1:
            dataset, items = next(iter(raw.items()))
            if isinstance(items, list):
                expanded.extend({"dataset": str(dataset), "value": item} for item in items)
                continue
        expanded.append(raw)
    return expanded


def _searchable(value: dict[str, object], member: TakeoutMember) -> str:
    return " ".join(
        (
            member.member_path.casefold(),
            *(str(key).casefold() for key in value),
            str(value.get("dataset", "")).casefold(),
            str(value.get("type", "")).casefold(),
        )
    )


def _classification(
    product_key: str, value: dict[str, object], member: TakeoutMember
) -> tuple[str, str]:
    searchable = _searchable(value, member)
    if product_key == "access_log_activity":
        return "event", "account_access"
    if product_key in {"alerts", "discover", "news", "search_notifications"}:
        if any(term in searchable for term in ("setting", "subscription", "follow", "opt in")):
            return "record", "subscription"
        return "event", "notification"
    if product_key in {"assignments", "classroom"}:
        if any(term in searchable for term in ("submission", "grade", "activity")):
            return "event", "education_activity"
        if "course" in searchable or "classroom" in searchable:
            return "record", "classroom_course"
        return "record", "assignment"
    if product_key in {"blogger", "help_communities", "search_contributions"}:
        return "record", "contribution"
    if product_key == "chrome":
        if "device info" in searchable:
            return "record", "device"
        if any(term in searchable for term in ("bookmark", "url", "autofill")):
            return "record", "saved_item"
        return "record", "account"
    if product_key == "google_developers":
        return "record", "developer_resource"
    if product_key == "youtube":
        if "message id" in searchable:
            return "event", "youtube_chat_message"
        if "comment id" in searchable:
            return "record", "contribution"
        if "channel id" in searchable and any(
            term in searchable for term in ("channel url", "subscription")
        ):
            return "record", "subscription"
        if "playlist id" in searchable:
            return "record", "saved_list"
        if "video id" in searchable:
            return "record", "saved_item"
        return "record", "account"
    if product_key == "appeal_content":
        return "record", "document"
    if product_key == "arts_culture":
        return "record", "saved_item"
    if product_key in {"google_ads", "business_profile"}:
        if "review" in searchable:
            return "record", "review"
        if any(term in searchable for term in ("activity", "click", "impression")):
            return "event", "business_activity"
        return "record", "business_profile"
    if product_key == "google_earth":
        return "record", "map" if "document" in searchable or "map" in searchable else "place"
    if product_key in {"google_feedback", "product_surveys"}:
        return "record", "survey_response"
    if product_key == "google_health":
        return "record", "health_record"
    if product_key in {"play_games", "play_movies_tv"}:
        if any(term in searchable for term in ("activity", "played", "watched")):
            return "event", "entertainment_activity"
        return "record", "game_record" if product_key == "play_games" else "saved_item"
    if product_key == "workspace_marketplace":
        return "record", "subscription"
    if product_key == "tenor":
        if "search" in searchable:
            return "event", "tenor_search"
        return "record", "saved_item"
    return "record", f"{product_key}_record"


def _provider_identity(value: dict[str, object], *, prefix: str) -> str:
    folded = {
        re.sub(r"[^a-z0-9]", "", str(key).casefold()): item for key, item in value.items()
    }
    for name in (
        "commentid",
        "messageid",
        "playlistid",
        "videoid",
        "channelid",
        "gaiaid",
        "profileid",
        "extensionid",
        "guid",
        "id",
        "accountname",
    ):
        candidate = folded.get(name)
        if isinstance(candidate, (str, int)) and not isinstance(candidate, bool):
            cleaned = str(candidate).strip()
            if cleaned:
                return f"provider:{cleaned}:{prefix}"
    return _identity(value, prefix=prefix)


def _record(
    member: TakeoutMember,
    value: dict[str, object],
    *,
    record_type: str,
    source_id: str,
    occurred_at: object,
) -> NormalizedRecord:
    authority_at, authority_kind = _authority(member, occurred_at)
    return NormalizedRecord(
        source="google",
        record_type=record_type,
        source_record_namespace=(
            f"google_takeout/{member.product_key}/{record_type}/{member.extension.removeprefix('.') or 'none'}"
        ),
        source_record_id=source_id,
        title=_first_string(
            value,
            (
                "title",
                "name",
                "surveyTitle",
                "productName",
                "Channel Title",
                "Channel Title (Original)",
                "Video Title (Original)",
            ),
        ),
        text=_text_projection(value),
        occurred_at=occurred_at,
        actor_identity="account_owner",
        metadata={
            "google": value,
            "_pdl": {
                "semantic_adapter": "long_tail_semantic",
                "source_path_token": _path_token(member),
            },
        },
        provider_updated_at=occurred_at,
        authority_at=authority_at,
        authority_kind=authority_kind,
    )


def _event(
    member: TakeoutMember,
    value: dict[str, object],
    *,
    event_type: str,
    source_id: str,
    occurred_at: object,
) -> NormalizedEvent:
    authority_at, authority_kind = _authority(member, occurred_at)
    return NormalizedEvent(
        occurred_at=occurred_at,
        source="google",
        event_type=event_type,
        source_record_namespace=(
            f"google_takeout/{member.product_key}/{event_type}/{member.extension.removeprefix('.') or 'none'}"
        ),
        source_record_id=source_id,
        actor_identity="account_owner",
        text=_text_projection(value),
        metadata={
            "google": value,
            "_pdl": {
                "semantic_adapter": "long_tail_semantic",
                "source_path_token": _path_token(member),
            },
        },
        provider_updated_at=occurred_at,
        authority_at=authority_at,
        authority_kind=authority_kind,
    )


def _structured(
    content: bytes, member: TakeoutMember
) -> tuple[list[NormalizedRecord], list[NormalizedEvent]]:
    product_key = member.product_key
    if product_key is None:
        raise GoogleAdapterError("long-tail adapter requires a known product")
    values = _expand_values(_decode(content, member), product_key)
    records: list[NormalizedRecord] = []
    events: list[NormalizedEvent] = []
    seen: dict[str, int] = {}
    path_token = _path_token(member)
    for index, value in enumerate(values):
        object_kind, semantic_type = _classification(product_key, value, member)
        source_id = _dedupe_identity(
            _provider_identity(value, prefix=f"{path_token}:{index}"), seen
        )
        occurred_at = _time(value, _EVENT_TIME_FIELDS)
        if object_kind == "event":
            events.append(
                _event(
                    member,
                    value,
                    event_type=semantic_type,
                    source_id=source_id,
                    occurred_at=occurred_at,
                )
            )
        else:
            records.append(
                _record(
                    member,
                    value,
                    record_type=semantic_type,
                    source_id=source_id,
                    occurred_at=occurred_at,
                )
            )
    return records, events


@dataclass(frozen=True)
class LongTailStructuredAdapter:
    adapter_name: str = "long_tail_semantic"
    parser_version: str = "google-long-tail-v1"
    product_keys: tuple[str, ...] = LONG_TAIL_PRODUCTS
    supported_patterns: tuple[str, ...] = (
        *(f"**/*{extension}" for extension in STRUCTURED_EXTENSIONS),
        "Takeout/Google Workspace Marketplace/*",
    )
    support_level: SupportLevel = "fully_structured"
    representation_kind: str = "records+events"
    is_fallback: bool = False

    def probe(self, member: TakeoutMember) -> MatchResult:
        structured = member.extension in STRUCTURED_EXTENSIONS or (
            member.product_key == "workspace_marketplace" and member.extension == ""
        )
        return MatchResult(
            member.product_key in self.product_keys
            and structured
            and member.candidate_adapter != "my_activity"
            and member.uncompressed_size <= SEMANTIC_MAX_MEMBER_BYTES
            and not member.is_directory
            and member.accounting_category == "classified",
            "bounded long-tail semantic representation",
            requires_byte_level_inspection=True,
        )

    def parse(self, stream: BinaryIO, member: TakeoutMember) -> ParsedExport:
        content = _read_bounded(stream, member)
        try:
            records, events = _structured(content, member)
        except (UnicodeDecodeError, json.JSONDecodeError, csv.Error, ValueError) as error:
            if isinstance(error, GoogleAdapterError):
                raise
            raise GoogleAdapterError(
                f"could not parse long-tail member {member.member_path}"
            ) from error
        return _parsed_export(
            member=member,
            content=content,
            parser_version=self.parser_version,
            category=(
                f"takeout/{member.product_key}/semantic/{member.extension.removeprefix('.') or 'none'}"
            ),
            records=records,
            events=events,
        )


@dataclass(frozen=True)
class LongTailAssetAdapter:
    adapter_name: str = "long_tail_asset"
    parser_version: str = "google-long-tail-asset-v1"
    product_keys: tuple[str, ...] = LONG_TAIL_PRODUCTS
    supported_patterns: tuple[str, ...] = ("**/*",)
    support_level: SupportLevel = "fully_structured"
    representation_kind: str = "assets"
    is_fallback: bool = False

    def probe(self, member: TakeoutMember) -> MatchResult:
        structured = member.candidate_adapter == "my_activity" or (
            (
                member.extension in STRUCTURED_EXTENSIONS
                or (member.product_key == "workspace_marketplace" and member.extension == "")
            )
            and member.uncompressed_size <= SEMANTIC_MAX_MEMBER_BYTES
        )
        return MatchResult(
            member.product_key in self.product_keys
            and not structured
            and not member.is_directory
            and member.accounting_category == "classified",
            "streamed external long-tail asset representation",
            requires_byte_level_inspection=True,
        )

    def parse(self, stream: BinaryIO, member: TakeoutMember) -> ParsedExport:
        digest = hashlib.sha256()
        size = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
        if size != member.uncompressed_size:
            raise GoogleAdapterError("asset size changed while reading")
        content_sha256 = digest.hexdigest()
        parsed = _parsed_export(
            member=member,
            content=b"",
            parser_version=self.parser_version,
            category=f"takeout/{member.product_key}/semantic/asset",
            assets=(
                _asset(
                    member,
                    sha256=content_sha256,
                    size_bytes=size,
                    metadata={"semantic_adapter": "long_tail_asset"},
                ),
            ),
        )
        return parsed.model_copy(update={"sha256": content_sha256, "size_bytes": size})


@dataclass(frozen=True)
class MyActivityAssetAdapter:
    """Index provider-supplied My Activity attachments without re-parsing activity HTML."""

    adapter_name: str = "my_activity_asset"
    parser_version: str = "google-my-activity-asset-v1"
    product_keys: tuple[str, ...] = ("my_activity",)
    supported_patterns: tuple[str, ...] = ("**/*",)
    support_level: SupportLevel = "fully_structured"
    representation_kind: str = "assets"
    is_fallback: bool = False

    def probe(self, member: TakeoutMember) -> MatchResult:
        return MatchResult(
            member.product_key == "my_activity"
            and member.candidate_adapter != "my_activity"
            and not member.is_directory
            and member.accounting_category == "classified",
            "streamed external My Activity attachment representation",
            requires_byte_level_inspection=True,
        )

    def parse(self, stream: BinaryIO, member: TakeoutMember) -> ParsedExport:
        digest = hashlib.sha256()
        size = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
        if size != member.uncompressed_size:
            raise GoogleAdapterError("asset size changed while reading")
        content_sha256 = digest.hexdigest()
        parsed = _parsed_export(
            member=member,
            content=b"",
            parser_version=self.parser_version,
            category="takeout/my_activity/semantic/asset",
            assets=(
                _asset(
                    member,
                    sha256=content_sha256,
                    size_bytes=size,
                    metadata={"semantic_adapter": "my_activity_asset"},
                ),
            ),
        )
        return parsed.model_copy(update={"sha256": content_sha256, "size_bytes": size})


PR11_ADAPTERS = (
    LongTailStructuredAdapter(),
    LongTailAssetAdapter(),
    MyActivityAssetAdapter(),
)
