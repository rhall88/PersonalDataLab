from __future__ import annotations

import codecs
import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
from zipfile import BadZipFile, ZipFile

from pydantic import ValidationError

from .fingerprints import FingerprintError, canonical_json_bytes
from .models import NormalizedEvent, ParsedExport

SOURCE = "google"
PARSER_VERSION = "google-my-activity-v1"
_READ_SIZE = 1024 * 1024
_TAKEOUT_COMPONENT = re.compile(
    r"takeout-(\d{8}T\d{6}Z)(?:-\d{3})?(?:\.zip)?",
    re.IGNORECASE,
)
_ACTIVITY_NAMES = {
    "myactivityjson",
    "myactivityhtml",
    "watchhistoryjson",
    "watchhistoryhtml",
    "searchhistoryjson",
    "searchhistoryhtml",
}


class GoogleExportError(ValueError):
    """Raised when a Google activity export cannot be parsed safely."""


def _normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _is_activity_filename(value: str) -> bool:
    path = PurePosixPath(value.replace("\\", "/"))
    return _normalized_name(path.name) in _ACTIVITY_NAMES


def _takeout_identity(path: Path) -> tuple[datetime | None, str | None]:
    for part in reversed(path.parts):
        match = _TAKEOUT_COMPONENT.fullmatch(part)
        if match is None:
            continue
        value = match.group(1).upper()
        try:
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC), value
        except ValueError:
            return None, None
    return None, None


def _timestamp(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if abs(numeric) >= 100_000_000_000:
            numeric /= 1000
        try:
            return datetime.fromtimestamp(numeric, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _text_values(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        cleaned = " ".join(value.split())
        if cleaned:
            yield cleaned
    elif isinstance(value, list):
        for item in value:
            yield from _text_values(item)
    elif isinstance(value, dict):
        for key in sorted(value):
            yield from _text_values(value[key])


def _activity_text(record: dict[str, Any]) -> str | None:
    values: list[str] = []
    for key in ("title", "subtitles", "description"):
        for value in _text_values(record.get(key)):
            if value not in values:
                values.append(value)
    return "\n".join(values) or None


def _contains_phrase(tokens: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    width = len(phrase)
    return any(tokens[index : index + width] == phrase for index in range(len(tokens) - width + 1))


def _product_key_from_evidence(evidence: Iterable[str]) -> str | None:
    token_groups = tuple(
        tuple(re.findall(r"[a-z0-9]+", value.casefold()))
        for value in evidence
        if value.strip()
    )
    if any("youtube" in tokens for tokens in token_groups):
        return "youtube"
    if any("maps" in tokens for tokens in token_groups):
        return "maps"
    if any("search" in tokens for tokens in token_groups):
        return "search"
    if any("chrome" in tokens for tokens in token_groups):
        return "chrome"
    if any("shopping" in tokens for tokens in token_groups):
        return "shopping"
    if any("play" in tokens for tokens in token_groups):
        return "play"
    if any(
        _contains_phrase(tokens, ("ad", "center"))
        or _contains_phrase(tokens, ("my", "ad"))
        or ("google" in tokens and "ads" in tokens)
        for tokens in token_groups
    ):
        return "ads"
    return None


_PRODUCT_EVENT_TYPES = {
    "youtube": "youtube_activity",
    "maps": "maps_activity",
    "search": "search_activity",
    "chrome": "browser_activity",
    "shopping": "shopping_activity",
    "play": "play_activity",
    "ads": "ad_activity",
}


def _activity_event_type(
    record: dict[str, Any], path_product: str | None
) -> tuple[str, bool]:
    record_evidence = (
        value
        for key in ("header", "products", "activityControls")
        for value in _text_values(record.get(key))
    )
    record_product = _product_key_from_evidence(record_evidence)
    if record_product is not None:
        return _PRODUCT_EVENT_TYPES[record_product], False
    if path_product is not None:
        return _PRODUCT_EVENT_TYPES[path_product], True
    return "google_activity", False


def _path_product(source_name: str) -> str | None:
    parts = list(PurePosixPath(source_name.replace("\\", "/")).parts)
    normalized = [_normalized_name(part) for part in parts]
    directory_parts = normalized[:-1]
    if "myactivity" in directory_parts:
        index = len(directory_parts) - 1 - directory_parts[::-1].index("myactivity")
        if index + 1 < len(parts) - 1:
            product = _product_key_from_evidence((parts[index + 1],))
            if product is not None:
                return product
    if "history" in directory_parts:
        index = len(directory_parts) - 1 - directory_parts[::-1].index("history")
        if index > 0:
            product = _product_key_from_evidence((parts[index - 1],))
            if product is not None:
                return product
    leaf = normalized[-1] if normalized else ""
    if leaf in {"watchhistoryjson", "watchhistoryhtml"}:
        return "youtube"
    if leaf in {"searchhistoryjson", "searchhistoryhtml"}:
        return "search"
    return None


def _is_archive_activity_path(value: str) -> bool:
    path = PurePosixPath(value.replace("\\", "/"))
    leaf = _normalized_name(path.name)
    if leaf not in _ACTIVITY_NAMES:
        return False
    directories = [_normalized_name(part) for part in path.parts[:-1]]
    if "myactivity" in directories:
        return True
    if leaf in {"watchhistoryjson", "watchhistoryhtml", "searchhistoryjson", "searchhistoryhtml"}:
        for index, part in enumerate(directories):
            if part != "history" or index == 0:
                continue
            if _product_key_from_evidence((path.parts[index - 1],)) is not None:
                return True
    return False


def _activity_category(source_name: str, path_product: str | None) -> str:
    product = path_product or "generic"
    source_format = "json" if source_name.casefold().endswith(".json") else "html"
    return f"my_activity/{product}/{source_format}"


def _record_id(record: dict[str, Any], duplicate_number: int) -> str:
    digest = hashlib.sha256(canonical_json_bytes(record)).hexdigest()
    suffix = "" if duplicate_number == 1 else f":{duplicate_number}"
    return f"activity:{digest}{suffix}"


def _normalize_activity(
    record: Any,
    *,
    index: int,
    path_product: str | None,
    exported_at: datetime | None,
    duplicate_counts: dict[bytes, int],
) -> NormalizedEvent:
    if not isinstance(record, dict):
        raise GoogleExportError(f"activity record at index {index} must be an object")
    if not any(key in record for key in ("title", "time", "header", "products")):
        raise GoogleExportError(f"activity record at index {index} has no recognized fields")

    identity = hashlib.sha256(canonical_json_bytes(record)).digest()
    duplicate_counts[identity] = duplicate_counts.get(identity, 0) + 1
    raw_time = record.get("time")
    occurred_at = _timestamp(raw_time)
    event_type, used_path_product = _activity_event_type(record, path_product)
    metadata = {"google": {key: value for key, value in record.items() if key != "time"}}
    adapter_metadata: dict[str, Any] = {}
    if raw_time is not None and occurred_at is None:
        adapter_metadata["unparsed_time"] = raw_time
    if used_path_product:
        adapter_metadata["takeout_product"] = path_product
    if adapter_metadata:
        metadata["_pdl"] = adapter_metadata

    return NormalizedEvent(
        occurred_at=occurred_at,
        source=SOURCE,
        event_type=event_type,
        source_record_namespace="activity",
        actor_identity="account_owner",
        text=_activity_text(record),
        metadata=metadata,
        source_record_id=_record_id(record, duplicate_counts[identity]),
        authority_at=exported_at,
        authority_kind="exported_at" if exported_at is not None else "unknown",
    )


def _iter_json_array(stream: BinaryIO, digest: Any) -> Iterator[Any]:
    decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
    parser = json.JSONDecoder()
    buffer = ""
    position = 0
    eof = False

    def read_more() -> bool:
        nonlocal buffer, eof
        chunk = stream.read(_READ_SIZE)
        if chunk:
            digest.update(chunk)
            buffer += decoder.decode(chunk)
            return True
        if not eof:
            buffer += decoder.decode(b"", final=True)
            eof = True
        return False

    def require_character() -> str:
        nonlocal position
        while True:
            while position < len(buffer) and buffer[position].isspace():
                position += 1
            if position < len(buffer):
                return buffer[position]
            if eof or not read_more():
                raise GoogleExportError("activity JSON ended unexpectedly")

    if require_character() != "[":
        raise GoogleExportError("activity JSON must contain a top-level array")
    position += 1
    first = True
    while True:
        character = require_character()
        if character == "]":
            position += 1
            while read_more():
                pass
            if buffer[position:].strip():
                raise GoogleExportError("activity JSON has content after the top-level array")
            return
        if not first:
            if character != ",":
                raise GoogleExportError("activity JSON array is missing a comma")
            position += 1
            require_character()
        while True:
            try:
                value, end = parser.raw_decode(buffer, position)
            except json.JSONDecodeError as error:
                if eof or not read_more():
                    raise GoogleExportError("invalid activity JSON") from error
                continue
            position = end
            yield value
            first = False
            if position > _READ_SIZE:
                buffer = buffer[position:]
                position = 0
            break


class _ActivityHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[tuple[str, tuple[str, ...]]] = []
        self._card_depth = 0
        self._text: list[str] = []
        self._links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "div" and "outer-cell" in classes and self._card_depth == 0:
            self._card_depth = 1
            self._text = []
            self._links = []
            return
        if self._card_depth:
            if tag == "div":
                if self._text:
                    self._text.append("\n")
                self._card_depth += 1
            if tag == "br":
                self._text.append("\n")
            if tag == "a" and attributes.get("href"):
                self._links.append(str(attributes["href"]))

    def handle_endtag(self, tag: str) -> None:
        if tag != "div" or not self._card_depth:
            return
        if self._card_depth > 1:
            self._text.append("\n")
        self._card_depth -= 1
        if self._card_depth == 0:
            text = "\n".join(
                line.strip() for line in "".join(self._text).splitlines() if line.strip()
            )
            if text:
                self.cards.append((text, tuple(dict.fromkeys(self._links))))

    def handle_data(self, data: str) -> None:
        if self._card_depth:
            self._text.append(data)


def _parse_html_cards(stream: BinaryIO, digest: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    decoder = codecs.getincrementaldecoder("utf-8-sig")("replace")
    parser = _ActivityHTMLParser()
    while chunk := stream.read(_READ_SIZE):
        digest.update(chunk)
        parser.feed(decoder.decode(chunk))
    parser.feed(decoder.decode(b"", final=True))
    parser.close()
    if not parser.cards:
        raise GoogleExportError("activity HTML contains no recognizable activity cards")
    return tuple(parser.cards)


def _normalize_html_card(
    card: tuple[str, tuple[str, ...]],
    *,
    path_product: str | None,
    exported_at: datetime | None,
    duplicate_counts: dict[bytes, int],
) -> NormalizedEvent:
    text, links = card
    lines = [line for line in text.splitlines() if line]
    record: dict[str, Any] = {
        "header": lines[0] if len(lines) > 1 else None,
        "title": "\n".join(lines[1:]) if len(lines) > 1 else text,
    }
    if links:
        record["titleUrl"] = links[0]
        record["links"] = list(links)
    return _normalize_activity(
        record,
        index=0,
        path_product=path_product,
        exported_at=exported_at,
        duplicate_counts=duplicate_counts,
    )


def _parse_activity_stream(
    stream: BinaryIO,
    *,
    source_name: str,
    original_path: str,
    size_bytes: int,
    exported_at: datetime | None,
    export_id: str | None,
    path_product: str | None,
) -> ParsedExport:
    digest = hashlib.sha256()
    duplicate_counts: dict[bytes, int] = {}
    try:
        if source_name.casefold().endswith(".json"):
            events = tuple(
                _normalize_activity(
                    record,
                    index=index,
                    path_product=path_product,
                    exported_at=exported_at,
                    duplicate_counts=duplicate_counts,
                )
                for index, record in enumerate(_iter_json_array(stream, digest))
            )
        else:
            events = tuple(
                _normalize_html_card(
                    card,
                    path_product=path_product,
                    exported_at=exported_at,
                    duplicate_counts=duplicate_counts,
                )
                for card in _parse_html_cards(stream, digest)
            )
        return ParsedExport(
            source=SOURCE,
            parser_version=PARSER_VERSION,
            category=_activity_category(source_name, path_product),
            filename=source_name,
            original_path=original_path,
            sha256=digest.hexdigest(),
            size_bytes=size_bytes,
            exported_at=exported_at,
            export_id=export_id,
            snapshot_kind="partial",
            snapshot_scope="account/activity",
            events=events,
        )
    except (FingerprintError, UnicodeDecodeError, ValidationError) as error:
        raise GoogleExportError(
            f"could not normalize Google activity file {source_name}"
        ) from error


def _iter_zip_exports(
    path: Path, *, require_supported_member: bool = True
) -> Iterator[ParsedExport]:
    exported_at, export_id = _takeout_identity(path)
    found = False
    try:
        with ZipFile(path) as archive:
            for info in sorted(archive.infolist(), key=lambda item: item.filename.casefold()):
                if info.is_dir() or not _is_archive_activity_path(info.filename):
                    continue
                found = True
                original_path = f"{path}!/{info.filename}"
                try:
                    with archive.open(info) as stream:
                        yield _parse_activity_stream(
                            stream,
                            source_name=info.filename,
                            original_path=original_path,
                            size_bytes=info.file_size,
                            exported_at=exported_at,
                            export_id=export_id,
                            path_product=_path_product(info.filename),
                        )
                except RuntimeError as error:
                    raise GoogleExportError(
                        f"could not read Google activity member {info.filename} from {path}"
                    ) from error
    except BadZipFile as error:
        raise GoogleExportError(f"invalid ZIP archive: {path}") from error
    if not found and require_supported_member:
        raise GoogleExportError(f"no supported Google activity files found in {path}")


def _candidate_activity_files(path: Path) -> tuple[Path, ...]:
    if path.is_file():
        return (path,) if _is_activity_filename(path.name) else ()

    bases = [path]
    takeout = path / "Takeout"
    if takeout.is_dir():
        bases.insert(0, takeout)
    candidates: set[Path] = set()
    for base in bases:
        for child in base.iterdir():
            normalized = _normalized_name(child.name)
            if child.is_dir() and normalized == "myactivity":
                candidates.update(
                    item
                    for item in child.rglob("*")
                    if item.is_file() and _is_activity_filename(str(item))
                )
            if child.is_dir() and "youtube" in normalized:
                history = child / "history"
                if history.is_dir():
                    candidates.update(
                        item
                        for item in history.iterdir()
                        if item.is_file() and _is_activity_filename(str(item))
                    )
    if _normalized_name(path.name) in {"myactivity", "history"}:
        candidates.update(
            item
            for item in path.rglob("*")
            if item.is_file() and _is_activity_filename(str(item))
        )
    return tuple(sorted(candidates, key=lambda item: str(item).casefold()))


def _iter_file_exports(path: Path) -> Iterator[ParsedExport]:
    candidates = _candidate_activity_files(path)
    if not candidates:
        raise GoogleExportError(f"no supported Google activity files found under {path}")
    exported_at, export_id = _takeout_identity(path)
    for candidate in candidates:
        candidate_exported_at, candidate_export_id = _takeout_identity(candidate)
        with candidate.open("rb") as stream:
            yield _parse_activity_stream(
                stream,
                source_name=(
                    candidate.relative_to(path).as_posix() if path.is_dir() else candidate.name
                ),
                original_path=str(candidate),
                size_bytes=candidate.stat().st_size,
                exported_at=candidate_exported_at or exported_at,
                export_id=candidate_export_id or export_id,
                path_product=_path_product(candidate.as_posix()),
            )


def iter_google_exports(source_path: str | Path) -> Iterator[ParsedExport]:
    """Yield supported Google activity files without loading the whole Takeout at once."""

    path = Path(source_path).expanduser().resolve(strict=True)
    if path.is_file() and path.suffix.casefold() == ".zip":
        yield from _iter_zip_exports(path)
        return
    if path.is_dir():
        archives = tuple(
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
        if archives:
            found = False
            for archive in archives:
                for parsed in _iter_zip_exports(archive, require_supported_member=False):
                    found = True
                    yield parsed
            if not found:
                raise GoogleExportError(
                    f"no supported Google activity files found in Takeout archives under {path}"
                )
            return
    yield from _iter_file_exports(path)


def parse_google_export(source_path: str | Path) -> tuple[ParsedExport, ...]:
    """Parse a Google source eagerly; the CLI uses ``iter_google_exports`` for bounded memory."""

    return tuple(iter_google_exports(source_path))
