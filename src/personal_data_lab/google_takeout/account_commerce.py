from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
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

ACCOUNT_COMMERCE_PRODUCTS = (
    "google_account",
    "google_one",
    "google_pay",
    "google_wallet",
    "purchases_reservations",
    "google_shopping",
    "google_store",
    "play_store",
    "google_finance",
    "google_fi",
    "pixel",
    "android_device_configuration",
    "home_app",
    "nest",
    "profile",
)
STRUCTURED_EXTENSIONS = (".json", ".jsonl", ".csv", ".html", ".htm")

_DEFAULT_RECORD_TYPES = {
    "google_account": "account",
    "google_one": "subscription",
    "google_pay": "payment",
    "google_wallet": "payment",
    "purchases_reservations": "purchase",
    "google_shopping": "saved_item",
    "google_store": "purchase",
    "google_finance": "saved_item",
    "google_fi": "account",
    "pixel": "device",
    "android_device_configuration": "device",
    "home_app": "home_device",
    "nest": "home_device",
    "profile": "account",
}


def _decode(content: bytes, extension: str) -> tuple[list[object], list[str]]:
    if extension != ".jsonl":
        return _decode_structured(content, extension), []
    values: list[object] = []
    warnings: list[str] = []
    for line_number, raw_line in enumerate(content.decode("utf-8-sig").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            values.append(json.loads(raw_line))
            continue
        except json.JSONDecodeError:
            pass
        row = next(csv.reader(io.StringIO(raw_line)))
        if len(row) == 1:
            try:
                values.append(json.loads(row[0]))
                continue
            except json.JSONDecodeError as error:
                decode_error = error
        else:
            decode_error = json.JSONDecodeError("CSV-wrapped JSON has multiple fields", raw_line, 0)
        values.append(
            {
                "raw_provider_line": row[0] if len(row) == 1 else raw_line,
                "decode_error": {
                    "line": decode_error.lineno,
                    "column": decode_error.colno,
                    "message": decode_error.msg,
                },
            }
        )
        warnings.append(f"Nest JSONL line {line_number} retained as opaque provider text")
    return values, warnings


def _flatten_datasets(values: list[object]) -> list[dict[str, object]]:
    flattened: list[dict[str, object]] = []
    for raw in values:
        if not isinstance(raw, dict):
            flattened.append({"value": raw})
            continue
        if not raw:
            continue
        if len(raw) == 1:
            dataset, items = next(iter(raw.items()))
            if isinstance(items, list):
                for item in items:
                    flattened.append(
                        {
                            "dataset": str(dataset),
                            "value": item,
                        }
                    )
                continue
        flattened.append(raw)
    return flattened


def _make_record(
    member: TakeoutMember,
    value: dict[str, object],
    *,
    record_type: str,
    namespace_suffix: str,
    source_id: str,
    occurred_at=None,
) -> NormalizedRecord:
    authority_at, authority_kind = _authority(member, occurred_at)
    return NormalizedRecord(
        source="google",
        record_type=record_type,
        source_record_namespace=(
            f"google_takeout/{member.product_key}/{namespace_suffix}/{member.extension.removeprefix('.')}"
        ),
        source_record_id=source_id,
        title=_first_string(value, ("title", "name", "Description", "Product")),
        text=_text_projection(value),
        occurred_at=occurred_at,
        actor_identity="account_owner",
        metadata={
            "google": value,
            "_pdl": {"semantic_adapter": "account_commerce_semantic"},
        },
        provider_updated_at=occurred_at,
        authority_at=authority_at,
        authority_kind=authority_kind,
    )


def _make_event(
    member: TakeoutMember,
    value: dict[str, object],
    *,
    event_type: str,
    namespace_suffix: str,
    source_id: str,
    occurred_at=None,
) -> NormalizedEvent:
    authority_at, authority_kind = _authority(member, occurred_at)
    return NormalizedEvent(
        occurred_at=occurred_at,
        source="google",
        event_type=event_type,
        source_record_namespace=(
            f"google_takeout/{member.product_key}/{namespace_suffix}/{member.extension.removeprefix('.')}"
        ),
        source_record_id=source_id,
        actor_identity="account_owner",
        text=_first_string(value, ("Description", "Product", "title", "name")),
        metadata={
            "google": value,
            "_pdl": {"semantic_adapter": "account_commerce_semantic"},
        },
        authority_at=authority_at,
        authority_kind=authority_kind,
    )


def _play_store(
    values: list[object], member: TakeoutMember
) -> tuple[list[NormalizedRecord], list[NormalizedEvent]]:
    records: list[NormalizedRecord] = []
    events: list[NormalizedEvent] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(values):
        wrapper = raw if isinstance(raw, dict) else {"value": raw}
        kind, payload = next(iter(wrapper.items())) if len(wrapper) == 1 else ("record", wrapper)
        value = payload if isinstance(payload, dict) else {"value": payload}
        source_id = _dedupe_identity(
            _identity(value, prefix=f"{kind}:{index}"),
            seen,
        )
        if kind == "install":
            events.append(
                _make_event(
                    member,
                    wrapper,
                    event_type="play_store_install",
                    namespace_suffix="install",
                    source_id=source_id,
                    occurred_at=_time(value, ("firstInstallationTime", "lastUpdateTime")),
                )
            )
        elif kind in {"purchaseHistory", "orderHistory"}:
            records.append(
                _make_record(
                    member,
                    wrapper,
                    record_type="purchase",
                    namespace_suffix=kind,
                    source_id=source_id,
                    occurred_at=_time(value, ("purchaseTime", "creationTime")),
                )
            )
        elif kind == "device":
            records.append(
                _make_record(
                    member,
                    wrapper,
                    record_type="device",
                    namespace_suffix="device",
                    source_id=source_id,
                    occurred_at=_time(value, ("lastTimeDeviceActive", "deviceRegistrationTime")),
                )
            )
        elif kind == "subscription":
            records.append(
                _make_record(
                    member,
                    wrapper,
                    record_type="subscription",
                    namespace_suffix="subscription",
                    source_id=source_id,
                    occurred_at=_time(value, ("expirationDate",)),
                )
            )
        elif kind == "libraryDoc":
            records.append(
                _make_record(
                    member,
                    wrapper,
                    record_type="saved_item",
                    namespace_suffix="library",
                    source_id=source_id,
                    occurred_at=_time(value, ("acquisitionTime",)),
                )
            )
        elif kind == "playPointsDetails" and isinstance(value.get("pointsHistory"), dict):
            points = value["pointsHistory"]
            events.append(
                _make_event(
                    member,
                    wrapper,
                    event_type="play_points_transaction",
                    namespace_suffix="points",
                    source_id=source_id,
                    occurred_at=_time(points, ("time",)),
                )
            )
        elif kind == "promotionHistory":
            events.append(
                _make_event(
                    member,
                    wrapper,
                    event_type="play_store_promotion",
                    namespace_suffix="promotion",
                    source_id=source_id,
                    occurred_at=_time(value, ("lastModificationTime",)),
                )
            )
        else:
            records.append(
                _make_record(
                    member,
                    wrapper,
                    record_type="account",
                    namespace_suffix=kind,
                    source_id=source_id,
                )
            )
    return records, events


def _structured(
    content: bytes, member: TakeoutMember
) -> tuple[list[NormalizedRecord], list[NormalizedEvent], list[str]]:
    values, warnings = _decode(content, member.extension)
    if member.product_key == "play_store":
        records, events = _play_store(values, member)
        return records, events, warnings
    flattened = _flatten_datasets(values)
    records: list[NormalizedRecord] = []
    events: list[NormalizedEvent] = []
    seen: dict[str, int] = {}
    for index, value in enumerate(flattened):
        source_id = _dedupe_identity(_identity(value, prefix=str(index)), seen)
        occurred_at = _time(
            value,
            (
                "Time",
                "Creation Timestamp",
                "createdAt",
                "creationTime",
                "timestamp",
                "eventTime",
            ),
        )
        if member.product_key == "google_pay" and member.extension == ".csv":
            events.append(
                _make_event(
                    member,
                    value,
                    event_type="payment_transaction",
                    namespace_suffix="transaction",
                    source_id=(
                        f"provider:{value['Transaction ID']}"
                        if value.get("Transaction ID")
                        else source_id
                    ),
                    occurred_at=occurred_at,
                )
            )
        elif member.product_key == "google_shopping" and member.extension == ".csv":
            events.append(
                _make_event(
                    member,
                    value,
                    event_type="shopping_review",
                    namespace_suffix="review",
                    source_id=source_id,
                    occurred_at=occurred_at,
                )
            )
        elif member.product_key == "nest" and member.extension == ".jsonl":
            events.append(
                _make_event(
                    member,
                    value,
                    event_type="nest_event",
                    namespace_suffix="event",
                    source_id=source_id,
                    occurred_at=occurred_at,
                )
            )
        else:
            record_type = _DEFAULT_RECORD_TYPES.get(member.product_key or "", "account")
            records.append(
                _make_record(
                    member,
                    value,
                    record_type=record_type,
                    namespace_suffix=record_type,
                    source_id=source_id,
                    occurred_at=occurred_at,
                )
            )
    return records, events, warnings


@dataclass(frozen=True)
class AccountCommerceStructuredAdapter:
    adapter_name: str = "account_commerce_semantic"
    parser_version: str = "google-account-commerce-v1"
    product_keys: tuple[str, ...] = ACCOUNT_COMMERCE_PRODUCTS
    supported_patterns: tuple[str, ...] = tuple(
        f"**/*{extension}" for extension in STRUCTURED_EXTENSIONS
    )
    support_level: SupportLevel = "fully_structured"
    representation_kind: str = "records+events"
    is_fallback: bool = False

    def probe(self, member: TakeoutMember) -> MatchResult:
        return MatchResult(
            member.product_key in self.product_keys
            and member.extension in STRUCTURED_EXTENSIONS
            and member.candidate_adapter != "my_activity"
            and member.uncompressed_size <= SEMANTIC_MAX_MEMBER_BYTES
            and not member.is_directory
            and member.accounting_category == "classified",
            "bounded account/commerce/device/home semantic representation",
            requires_byte_level_inspection=True,
        )

    def parse(self, stream: BinaryIO, member: TakeoutMember) -> ParsedExport:
        content = _read_bounded(stream, member)
        try:
            records, events, warnings = _structured(content, member)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            if isinstance(error, GoogleAdapterError):
                raise
            raise GoogleAdapterError(
                f"could not parse account/commerce member {member.member_path}"
            ) from error
        return _parsed_export(
            member=member,
            content=content,
            parser_version=self.parser_version,
            category=(
                f"takeout/{member.product_key}/semantic/{member.extension.removeprefix('.')}"
            ),
            records=records,
            events=events,
            warnings=warnings,
        )


@dataclass(frozen=True)
class AccountCommerceAssetAdapter:
    adapter_name: str = "account_commerce_asset"
    parser_version: str = "google-account-commerce-asset-v1"
    product_keys: tuple[str, ...] = ACCOUNT_COMMERCE_PRODUCTS
    supported_patterns: tuple[str, ...] = ("**/*",)
    support_level: SupportLevel = "fully_structured"
    representation_kind: str = "assets"
    is_fallback: bool = False

    def probe(self, member: TakeoutMember) -> MatchResult:
        structured = (
            member.candidate_adapter == "my_activity"
            or (
                member.extension in STRUCTURED_EXTENSIONS
                and member.uncompressed_size <= SEMANTIC_MAX_MEMBER_BYTES
            )
        )
        return MatchResult(
            member.product_key in self.product_keys
            and not structured
            and not member.is_directory
            and member.accounting_category == "classified",
            "streamed external account/commerce asset representation",
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
            assets=(_asset(member, sha256=content_sha256, size_bytes=size),),
        )
        return parsed.model_copy(update={"sha256": content_sha256, "size_bytes": size})


PR10_ADAPTERS = (AccountCommerceStructuredAdapter(), AccountCommerceAssetAdapter())
