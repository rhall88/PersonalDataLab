from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from .anthropic_importer import parse_anthropic_export
from .config import database_url
from .database import create_database_engine, import_openai_export, import_parsed_exports
from .google_importer import iter_google_exports
from .google_takeout.inventory import inspect_google, render_inventory_table
from .google_takeout.orchestrator import import_google_all
from .migrations import LATEST_SCHEMA_VERSION, migrate_database
from .openai_importer import parse_openai_export


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="personal-data-lab")
    subparsers = parser.add_subparsers(dest="command", required=True)
    openai = subparsers.add_parser("import-openai", help="import an OpenAI data export")
    openai.add_argument("source", help="ZIP, extracted directory, or conversations.json")
    openai.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    anthropic = subparsers.add_parser(
        "import-anthropic",
        help="import an Anthropic manifest and its local source archives",
    )
    anthropic.add_argument("manifest", help="manifest JSON beside the untouched ZIP archives")
    anthropic.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    google = subparsers.add_parser(
        "import-google",
        help="import Google My Activity from Takeout ZIPs or an extracted export",
    )
    google.add_argument(
        "source",
        help="Takeout ZIP, folder of split ZIPs, extracted Takeout, or MyActivity file",
    )
    google.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    inspect = subparsers.add_parser(
        "inspect-google",
        help="inventory every member of Takeout ZIPs or an extracted export",
    )
    inspect.add_argument("source", help="Takeout ZIP, folder of split ZIPs, or extracted Takeout")
    inspect.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "table"),
        default="json",
        help="output machine-readable JSON (default) or a human-readable table",
    )
    google_all = subparsers.add_parser(
        "import-google-all",
        help="inventory and import all members supported by registered Takeout adapters",
    )
    google_all.add_argument("source", help="Takeout ZIP, folder of split ZIPs, or extracted Takeout")
    google_all.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    migrate = subparsers.add_parser("migrate", help="upgrade an existing database schema")
    migrate.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    return parser


def _batch_summary(parsed_exports: Sequence, results: Sequence) -> dict:
    files = [
        parsed.model_dump(
            mode="json",
            include={
                "category",
                "filename",
                "original_path",
                "sha256",
                "size_bytes",
                "exported_at",
                "export_id",
                "parser_version",
            },
        )
        | result.model_dump(
            exclude={
                "source_records",
                "database_records",
                "source_assets",
                "database_assets",
                "inserted_records",
                "updated_records",
                "unchanged_records",
                "stale_records",
                "conflicted_records",
                "tombstoned_records",
                "new_record_versions",
                "reused_record_versions",
                "inserted_assets",
                "updated_assets",
                "unchanged_assets",
                "stale_assets",
                "conflicted_assets",
                "tombstoned_assets",
                "new_asset_versions",
                "reused_asset_versions",
            }
        )
        | {"reconciled": result.reconciled}
        for parsed, result in zip(parsed_exports, results, strict=True)
    ]
    return {
        "files": files,
        "source_files": len(files),
        "source_conversations": sum(result.source_conversations for result in results),
        "database_conversations": sum(result.database_conversations for result in results),
        "source_events": sum(result.source_events for result in results),
        "database_events": sum(result.database_events for result in results),
        "inserted_conversations": sum(result.inserted_conversations for result in results),
        "updated_conversations": sum(result.updated_conversations for result in results),
        "unchanged_conversations": sum(result.unchanged_conversations for result in results),
        "stale_conversations": sum(result.stale_conversations for result in results),
        "conflicted_conversations": sum(result.conflicted_conversations for result in results),
        "tombstoned_conversations": sum(result.tombstoned_conversations for result in results),
        "inserted_events": sum(result.inserted_events for result in results),
        "updated_events": sum(result.updated_events for result in results),
        "unchanged_events": sum(result.unchanged_events for result in results),
        "stale_events": sum(result.stale_events for result in results),
        "conflicted_events": sum(result.conflicted_events for result in results),
        "tombstoned_events": sum(result.tombstoned_events for result in results),
        "new_distinct_versions": sum(
            result.new_conversation_versions + result.new_event_versions for result in results
        ),
        "reused_versions": sum(
            result.reused_conversation_versions + result.reused_event_versions for result in results
        ),
        "short_circuited": all(result.short_circuited for result in results),
        "reconciled": all(result.reconciled for result in results),
    }


def _google_member_started(original_path: str, category: str, size_bytes: int) -> None:
    print(
        f"[google] parsing member={json.dumps(original_path)} "
        f"category={category} size_bytes={size_bytes}",
        file=sys.stderr,
        flush=True,
    )


def _google_member_parsed(parsed) -> None:
    print(
        f"[google] parsed member={json.dumps(parsed.original_path)} "
        f"category={parsed.category} size_bytes={parsed.size_bytes} "
        f"source_events={parsed.event_count}",
        file=sys.stderr,
        flush=True,
    )


def _google_member_imported(parsed, result) -> None:
    print(
        f"[google] imported member={json.dumps(parsed.original_path)} "
        f"category={parsed.category} source_events={result.source_events} "
        f"inserted={result.inserted_events} unchanged={result.unchanged_events} "
        f"conflicted={result.conflicted_events}",
        file=sys.stderr,
        flush=True,
    )


def _google_all_member_started(member, adapter) -> None:
    print(
        f"[google-all] parsing member={json.dumps(member.source_locator)} "
        f"product={member.product_key} adapter={adapter.adapter_name} "
        f"size_bytes={member.uncompressed_size}",
        file=sys.stderr,
        flush=True,
    )


def _google_all_member_imported(member, parsed, result) -> None:
    print(
        f"[google-all] imported member={json.dumps(member.source_locator)} "
        f"product={member.product_key} records={result.source_records} "
        f"events={result.source_events} conversations={result.source_conversations} "
        f"assets={result.source_assets} short_circuited={str(result.short_circuited).lower()} "
        f"conflicts="
        f"{result.conflicted_records + result.conflicted_events + result.conflicted_conversations + result.conflicted_assets}",
        file=sys.stderr,
        flush=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "import-openai":
        parsed = parse_openai_export(args.source)
        engine = create_database_engine(database_url(args.database_url))
        result = import_openai_export(engine, parsed)
        print(json.dumps(result.model_dump() | {"reconciled": result.reconciled}, indent=2))
        return 2 if result.has_conflicts else 0
    if args.command == "import-anthropic":
        parsed_exports = parse_anthropic_export(args.manifest)
        engine = create_database_engine(database_url(args.database_url))
        results = import_parsed_exports(engine, parsed_exports)
        print(json.dumps(_batch_summary(parsed_exports, results), indent=2))
        return 2 if any(result.has_conflicts for result in results) else 0
    if args.command == "import-google":
        engine = create_database_engine(database_url(args.database_url))
        parsed_exports = []
        results = []
        for parsed in iter_google_exports(
            args.source,
            on_member_start=_google_member_started,
        ):
            _google_member_parsed(parsed)
            result = import_parsed_exports(engine, (parsed,))[0]
            _google_member_imported(parsed, result)
            parsed_exports.append(parsed.model_copy(update={"events": (), "conversations": ()}))
            results.append(result)
        print(json.dumps(_batch_summary(parsed_exports, results), indent=2))
        return 2 if any(result.has_conflicts for result in results) else 0
    if args.command == "inspect-google":
        inventory = inspect_google(args.source)
        if args.output_format == "table":
            print(render_inventory_table(inventory))
        else:
            print(json.dumps(inventory.model_dump(mode="json"), indent=2))
        return 5 if inventory.unknown_members_count else 0
    if args.command == "import-google-all":
        engine = create_database_engine(database_url(args.database_url))
        summary = import_google_all(
            engine,
            args.source,
            on_member_start=_google_all_member_started,
            on_member_imported=_google_all_member_imported,
        )
        for product in summary.coverage.values():
            for failed_member in product.failed_members:
                print(
                    f"[google-all] failed member={json.dumps(failed_member)}",
                    file=sys.stderr,
                    flush=True,
                )
        print(
            json.dumps(
                summary.model_dump(mode="json") | {"exit_code": summary.exit_code},
                indent=2,
            )
        )
        return summary.exit_code
    if args.command == "migrate":
        engine = create_database_engine(database_url(args.database_url))
        applied = migrate_database(engine)
        print(
            json.dumps(
                {
                    "schema_version": LATEST_SCHEMA_VERSION,
                    "applied": list(applied),
                    "changed": bool(applied),
                },
                indent=2,
            )
        )
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
