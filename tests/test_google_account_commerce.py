from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from personal_data_lab.google_takeout.adapters import (
    DEFAULT_ADAPTER_REGISTRY,
    open_takeout_member,
)
from personal_data_lab.google_takeout.inventory import inspect_google


def _zip(path: Path, members: dict[str, str | bytes]) -> Path:
    with ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


def _member(path: Path, suffix: str):
    return next(
        item for item in inspect_google(path).members if item.member_path.endswith(suffix)
    )


def _parse(path: Path, suffix: str):
    member = _member(path, suffix)
    adapter = DEFAULT_ADAPTER_REGISTRY.resolve(member)
    assert adapter is not None
    with open_takeout_member(member) as stream:
        return adapter, adapter.parse(stream, member)


def test_google_pay_transactions_use_provider_ids_and_preserve_fields(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Google Pay/transactions.csv": (
                "Time,Transaction ID,Description,Memo,Payment method,Status,Amount\n"
                "2026-09-16T12:00:00Z,transaction-1,Synthetic payment,Test,Card,Complete,$1.00\n"
            )
        },
    )

    adapter, parsed = _parse(source, "transactions.csv")

    assert adapter.adapter_name == "account_commerce_semantic"
    assert parsed.events[0].event_type == "payment_transaction"
    assert parsed.events[0].source_record_id == "provider:transaction-1"
    assert parsed.events[0].metadata["google"]["Amount"] == "$1.00"


def test_play_store_distinguishes_installs_purchases_devices_and_points(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Google Play Store/history.json": json.dumps(
                [
                    {
                        "install": {
                            "doc": {"title": "Synthetic app"},
                            "firstInstallationTime": "2026-09-16T12:00:00Z",
                        }
                    },
                    {
                        "purchaseHistory": {
                            "doc": {"title": "Synthetic purchase"},
                            "purchaseTime": "2026-09-16T12:01:00Z",
                            "invoicePrice": "$1.00",
                        }
                    },
                    {
                        "device": {
                            "deviceRegistrationTime": "2026-09-16T12:02:00Z",
                            "mostRecentData": {"modelName": "Synthetic device"},
                        }
                    },
                    {
                        "playPointsDetails": {
                            "pointsHistory": {
                                "transactionId": "points-1",
                                "time": "2026-09-16T12:03:00Z",
                                "pointsChange": "1",
                            }
                        }
                    },
                ]
            )
        },
    )

    _, parsed = _parse(source, "history.json")

    assert {record.record_type for record in parsed.records} == {"purchase", "device"}
    assert {event.event_type for event in parsed.events} == {
        "play_store_install",
        "play_points_transaction",
    }


def test_home_and_nest_keep_dataset_and_jsonl_event_semantics(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Home App/home.json": json.dumps(
                {
                    "Home App Data": [
                        {"full_structures": [{"name": "Synthetic home", "future": True}]}
                    ]
                }
            ),
            "Takeout/Nest/events.jsonl": (
                json.dumps(
                    {
                        "eventTime": "2026-09-16T12:00:00Z",
                        "type": "synthetic_event",
                        "future": "preserved",
                    }
                )
                + "\n"
            ),
        },
    )

    _, home = _parse(source, "home.json")
    _, nest = _parse(source, "events.jsonl")

    assert home.records[0].record_type == "home_device"
    assert home.records[0].metadata["google"]["dataset"] == "Home App Data"
    assert nest.events[0].event_type == "nest_event"
    assert nest.events[0].metadata["google"]["future"] == "preserved"


def test_profile_and_wallet_binary_content_stays_external(tmp_path: Path) -> None:
    portrait = b"synthetic-portrait"
    wallet = b"%PDF-synthetic"
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Profile/profile.json": json.dumps(
                {
                    "displayName": "Synthetic Person",
                    "name": {"givenName": "Synthetic", "familyName": "Person"},
                    "emails": [{"value": "person@example.invalid"}],
                }
            ),
            "Takeout/Profile/profile.jpg": portrait,
            "Takeout/Google Wallet/pass.pdf": wallet,
        },
    )

    _, profile = _parse(source, "profile.json")
    asset_adapter, image = _parse(source, "profile.jpg")
    _, pdf = _parse(source, "pass.pdf")

    assert profile.records[0].record_type == "account"
    assert asset_adapter.adapter_name == "account_commerce_asset"
    assert image.assets[0].sha256 == hashlib.sha256(portrait).hexdigest()
    assert pdf.assets[0].sha256 == hashlib.sha256(wallet).hexdigest()


def test_absent_account_commerce_product_contracts_are_routable(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Google Fi Wireless/account.json": json.dumps(
                {"name": "Synthetic Fi account"}
            ),
            "Takeout/Pixel/device.json": json.dumps(
                {"name": "Synthetic Pixel", "createdAt": "2026-09-16T12:00:00Z"}
            ),
            "Takeout/Purchases & Reservations/purchases.json": json.dumps(
                [{"id": "purchase-1", "title": "Synthetic purchase"}]
            ),
        },
    )

    _, google_fi = _parse(source, "account.json")
    _, pixel = _parse(source, "device.json")
    _, purchases = _parse(source, "purchases.json")

    assert google_fi.records[0].record_type == "account"
    assert pixel.records[0].record_type == "device"
    assert purchases.records[0].record_type == "purchase"


def test_nested_my_activity_in_google_pay_keeps_streaming_activity_route(
    tmp_path: Path,
) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Google Pay/My Activity/My Activity.html": (
                "<html><body><div class='mdl-grid'><div>Synthetic payment activity</div>"
                "<div>Sep 16, 2026, 12:00:00 PM UTC</div></div></body></html>"
            )
        },
    )

    member = _member(source, "My Activity.html")
    adapter = DEFAULT_ADAPTER_REGISTRY.resolve(member)

    assert adapter is not None
    assert adapter.adapter_name == "my_activity"


def test_nest_csv_wrapped_jsonl_and_malformed_provider_rows_are_explicit(
    tmp_path: Path,
) -> None:
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Nest/events.jsonl": (
                '"{\"\"id\"\": \"\"event-1\"\", \"\"eventTime\"\": '
                '\"\"2026-09-16T12:00:00Z\"\"}"\n'
                '"{\"\"id\"\": \"\"event-2\"\" \"\"eventTime\"\": \"\"bad\"\"}"\n'
            )
        },
    )

    _, parsed = _parse(source, "events.jsonl")

    assert parsed.event_count == 2
    assert parsed.events[0].metadata["google"]["id"] == "event-1"
    assert "raw_provider_line" in parsed.events[1].metadata["google"]
    assert len(parsed.parse_warnings) == 1


def _doubly_escaped_nest_line(inner: dict) -> str:
    """Build a Nest JSONL line reproducing the real provider double-escaping bug.

    The retained export's Nest CSV-wrapped JSONL nests a JSON document as a string value
    using one extra layer of backslash-escaped quotes (`\\\\"` instead of `\\"`), which
    prematurely closes the JSON string and breaks the row.
    """
    inner_json = json.dumps(inner, separators=(",", ":"))
    properly_escaped = json.dumps(inner_json)
    buggy_value = properly_escaped.replace('\\"', '\\\\"')
    outer = '{"value":' + buggy_value + "}"
    return '"' + outer.replace('"', '""') + '"'


def test_nest_recovers_doubly_escaped_provider_rows_without_warnings(tmp_path: Path) -> None:
    inner = {"name": "Ada", "structures": [], "is_merged_with_gaia": True}
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {"Takeout/Nest/deltaforce_bucket_data.jsonl": _doubly_escaped_nest_line(inner) + "\n"},
    )

    _, parsed = _parse(source, "deltaforce_bucket_data.jsonl")

    assert parsed.event_count == 1
    assert "raw_provider_line" not in parsed.events[0].metadata["google"]
    assert parsed.events[0].metadata["google"]["value"] == inner
    assert parsed.parse_warnings == ()


def test_nest_double_escape_recovery_does_not_mask_genuine_syntax_errors(
    tmp_path: Path,
) -> None:
    # A genuinely malformed row (missing comma) that happens to contain the doubled
    # escape sequence must still fall through to the explicit opaque fallback rather
    # than being silently "recovered" into something incorrect.
    broken = '{"value":"{\\\\"a\\\\":1" "b":2}'
    source = _zip(
        tmp_path / "takeout-20260916T000000Z-001.zip",
        {
            "Takeout/Nest/events.jsonl": (
                '"' + broken.replace('"', '""') + '"\n'
            )
        },
    )

    _, parsed = _parse(source, "events.jsonl")

    assert "raw_provider_line" in parsed.events[0].metadata["google"]
    assert len(parsed.parse_warnings) == 1
