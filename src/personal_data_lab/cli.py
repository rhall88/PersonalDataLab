from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.engine import Engine, make_url

from . import __version__
from .anthropic_export.inventory import inspect_anthropic
from .anthropic_export.inventory import render_inventory_table as render_anthropic_table
from .anthropic_export.orchestrator import import_anthropic_all
from .apple_export.inventory import inspect_apple
from .apple_export.inventory import render_inventory_table as render_apple_table
from .apple_export.orchestrator import import_apple_all
from .config import (
    canonical_database_name,
    database_url,
    guard_acceptance_run_not_canonical,
    guard_canonical_import_not_test_like,
    is_test_like_database_name,
)
from .database import (
    create_database_engine,
    import_openai_export,
    import_parsed_exports,
    list_sibling_databases,
)
from .facebook_export.inventory import inspect_facebook
from .facebook_export.inventory import render_inventory_table as render_facebook_table
from .facebook_export.orchestrator import import_facebook_all
from .google_importer import iter_google_exports
from .google_takeout.inventory import inspect_google, render_inventory_table
from .google_takeout.orchestrator import import_google_all
from .imessages.inventory import inspect_imessages
from .imessages.inventory import render_inventory_table as render_imessages_table
from .imessages.orchestrator import import_imessages_all
from .migrations import LATEST_SCHEMA_VERSION, current_schema_version, migrate_database
from .openai_export.inventory import inspect_openai
from .openai_export.orchestrator import import_openai_all
from .openai_importer import parse_openai_export


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="personal-data-lab")
    subparsers = parser.add_subparsers(dest="command", required=True)
    openai = subparsers.add_parser("import-openai", help="import an official OpenAI data export")
    openai.add_argument(
        "source", help="official ZIP, extracted export, or legacy conversations.json"
    )
    openai.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    inspect_openai_parser = subparsers.add_parser(
        "inspect-openai",
        help="inventory every physical and logical member of an official OpenAI export",
    )
    inspect_openai_parser.add_argument("source", help="official OpenAI ZIP or extracted export")
    inspect_openai_parser.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "table"),
        default="json",
    )
    inspect_openai_parser.add_argument(
        "--include-paths",
        action="store_true",
        help="include potentially sensitive export member paths in JSON output",
    )
    anthropic = subparsers.add_parser(
        "import-anthropic",
        help="inventory and import an Anthropic manifest and its local source archives",
    )
    anthropic.add_argument("manifest", help="manifest JSON beside the untouched ZIP archives")
    anthropic.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    anthropic_all = subparsers.add_parser(
        "import-anthropic-all",
        help="alias for the production Anthropic manifest importer",
    )
    anthropic_all.add_argument(
        "manifest", help="manifest JSON beside the untouched ZIP archives"
    )
    anthropic_all.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    inspect_anthropic_parser = subparsers.add_parser(
        "inspect-anthropic",
        help="inventory an Anthropic manifest and every local archive member",
    )
    inspect_anthropic_parser.add_argument(
        "manifest", help="manifest JSON beside the untouched ZIP archives"
    )
    inspect_anthropic_parser.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "table"),
        default="json",
    )
    inspect_anthropic_parser.add_argument(
        "--include-paths",
        action="store_true",
        help="include potentially sensitive archive and member paths in JSON output",
    )
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
    google_all.add_argument(
        "source", help="Takeout ZIP, folder of split ZIPs, or extracted Takeout"
    )
    google_all.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    inspect_apple_parser = subparsers.add_parser(
        "inspect-apple",
        help="recursively inventory official Apple privacy-export ZIPs",
    )
    inspect_apple_parser.add_argument("source", help="Apple export ZIP or directory of ZIPs")
    inspect_apple_parser.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "table"),
        default="json",
    )
    inspect_apple_parser.add_argument(
        "--include-paths",
        action="store_true",
        help="include potentially sensitive member paths in JSON output",
    )
    apple_all = subparsers.add_parser(
        "import-apple-all",
        help="inventory and import all official Apple privacy-export data members",
    )
    apple_all.add_argument("source", help="Apple export ZIP or directory of ZIPs")
    apple_all.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    inspect_facebook_parser = subparsers.add_parser(
        "inspect-facebook",
        help='inventory an official Facebook "Download Your Information" export ZIP',
    )
    inspect_facebook_parser.add_argument("source", help="Facebook export ZIP or directory of ZIPs")
    inspect_facebook_parser.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "table"),
        default="json",
    )
    inspect_facebook_parser.add_argument(
        "--include-paths",
        action="store_true",
        help="include potentially sensitive member paths in JSON output",
    )
    facebook_all = subparsers.add_parser(
        "import-facebook-all",
        help="inventory and import all official Facebook export data members",
    )
    facebook_all.add_argument("source", help="Facebook export ZIP or directory of ZIPs")
    facebook_all.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    inspect_imessages_parser = subparsers.add_parser(
        "inspect-imessages",
        help="inventory independently acquired iMazing Messages CSV exports",
    )
    inspect_imessages_parser.add_argument(
        "source", help="Messages CSV or directory containing one or more exports"
    )
    inspect_imessages_parser.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "table"),
        default="json",
    )
    inspect_imessages_parser.add_argument(
        "--include-paths",
        action="store_true",
        help="include potentially sensitive relative paths in JSON output",
    )
    imessages_all = subparsers.add_parser(
        "import-imessages-all",
        help="import all independently acquired iMazing Messages CSV exports",
    )
    imessages_all.add_argument(
        "source", help="Messages CSV or directory containing one or more exports"
    )
    imessages_all.add_argument(
        "--timezone",
        required=True,
        help="IANA timezone used by the export's timezone-naive timestamps",
    )
    imessages_all.add_argument(
        "--device-label",
        help="optional acquisition label; no device identifier is inferred from CSV content",
    )
    imessages_all.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    migrate = subparsers.add_parser("migrate", help="upgrade an existing database schema")
    migrate.add_argument("--database-url", help="overrides DATABASE_URL from .env")
    doctor = subparsers.add_parser(
        "doctor",
        help="report the configured database target, schema version, and every "
        "sibling database on the same Postgres instance",
    )
    doctor.add_argument(
        "--format",
        dest="output_format",
        choices=("json", "table"),
        default="json",
        help="output machine-readable JSON (default) or a human-readable table",
    )
    doctor.add_argument("--database-url", help="overrides DATABASE_URL from .env")
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


def _apple_member_started(member, adapter) -> None:
    print(
        f"[apple-all] parsing member_id={member.member_id} "
        f"product={member.product_key} adapter={adapter.adapter_name} "
        f"size_bytes={member.uncompressed_size}",
        file=sys.stderr,
        flush=True,
    )


def _apple_member_imported(member, parsed, result) -> None:
    conflicts = (
        result.conflicted_records
        + result.conflicted_events
        + result.conflicted_assets
        + result.conflicted_relationships
    )
    print(
        f"[apple-all] imported member_id={member.member_id} product={member.product_key} "
        f"records={result.source_records} events={result.source_events} "
        f"assets={result.source_assets} relationships={result.source_relationships} "
        f"short_circuited={str(result.short_circuited).lower()} conflicts={conflicts}",
        file=sys.stderr,
        flush=True,
    )


def _facebook_member_started(member, adapter) -> None:
    print(
        f"[facebook-all] parsing member_id={member.member_id} "
        f"product={member.product_key} adapter={adapter.adapter_name} "
        f"size_bytes={member.uncompressed_size}",
        file=sys.stderr,
        flush=True,
    )


def _facebook_member_imported(member, parsed, result) -> None:
    conflicts = (
        result.conflicted_conversations
        + result.conflicted_records
        + result.conflicted_events
        + result.conflicted_assets
        + result.conflicted_relationships
    )
    print(
        f"[facebook-all] imported member_id={member.member_id} product={member.product_key} "
        f"conversations={result.source_conversations} records={result.source_records} "
        f"events={result.source_events} assets={result.source_assets} "
        f"relationships={result.source_relationships} "
        f"short_circuited={str(result.short_circuited).lower()} conflicts={conflicts}",
        file=sys.stderr,
        flush=True,
    )


def _imessages_member_started(member) -> None:
    print(
        f"[imessages] parsing member_id={member.member_id} "
        f"records={member.records} size_bytes={member.size_bytes}",
        file=sys.stderr,
        flush=True,
    )


def _imessages_member_imported(member, parsed, result) -> None:
    conflicts = (
        result.conflicted_conversations
        + result.conflicted_events
        + result.conflicted_records
        + result.conflicted_assets
        + result.conflicted_relationships
    )
    print(
        f"[imessages] imported member_id={member.member_id} category={parsed.category} "
        f"conversations={result.source_conversations} events={result.source_events} "
        f"records={result.source_records} assets={result.source_assets} "
        f"relationships={result.source_relationships} "
        f"short_circuited={str(result.short_circuited).lower()} conflicts={conflicts}",
        file=sys.stderr,
        flush=True,
    )


def _openai_member_started(member) -> None:
    print(
        f"[openai] parsing member_id={member.member_id} "
        f"category={member.accounting_category} size_bytes={member.size_bytes}",
        file=sys.stderr,
        flush=True,
    )


def _openai_member_imported(member, parsed, result) -> None:
    conflicts = (
        result.conflicted_conversations
        + result.conflicted_events
        + result.conflicted_records
        + result.conflicted_assets
        + result.conflicted_relationships
    )
    print(
        f"[openai] imported member_id={member.member_id} category={member.accounting_category} "
        f"conversations={result.source_conversations} events={result.source_events} "
        f"records={result.source_records} assets={result.source_assets} "
        f"relationships={result.source_relationships} "
        f"short_circuited={str(result.short_circuited).lower()} conflicts={conflicts}",
        file=sys.stderr,
        flush=True,
    )


def _anthropic_member_started(source_id: str, category: str, size_bytes: int) -> None:
    print(
        f"[anthropic] parsing source_id={source_id} "
        f"category={category} size_bytes={size_bytes}",
        file=sys.stderr,
        flush=True,
    )


def _anthropic_member_imported(source_id: str, parsed, result) -> None:
    conflicts = (
        result.conflicted_conversations
        + result.conflicted_events
        + result.conflicted_records
        + result.conflicted_assets
        + result.conflicted_relationships
    )
    print(
        f"[anthropic] imported source_id={source_id} category={parsed.category} "
        f"conversations={result.source_conversations} events={result.source_events} "
        f"records={result.source_records} assets={result.source_assets} "
        f"relationships={result.source_relationships} "
        f"short_circuited={str(result.short_circuited).lower()} conflicts={conflicts}",
        file=sys.stderr,
        flush=True,
    )


def _resolve_guarded_engine(explicit_url: str | None, *, canonical_import: bool) -> Engine:
    url = database_url(explicit_url)
    guard_acceptance_run_not_canonical(url)
    if canonical_import:
        guard_canonical_import_not_test_like(url)
    return create_database_engine(url)


def _classify_database_name(name: str) -> str:
    if name == canonical_database_name():
        return "canonical"
    if is_test_like_database_name(name):
        return "test-like"
    return "unclassified"


def _build_doctor_report(explicit_url: str | None) -> dict:
    # Deliberately does not call _resolve_guarded_engine: doctor must be able
    # to report on any target, including one the import guards would refuse.
    url = database_url(explicit_url)
    parsed = make_url(url)
    target_name = parsed.database or ""
    target_classification = _classify_database_name(target_name)
    pdl_env = os.getenv("PDL_ENV", "production")

    engine = create_database_engine(url)
    schema_version = current_schema_version(engine)
    siblings = [
        {
            "name": name,
            "size_bytes": size_bytes,
            "classification": _classify_database_name(name),
            "is_target": name == target_name,
        }
        for name, size_bytes in list_sibling_databases(engine)
    ]

    suspicious: list[str] = []
    if pdl_env not in {"production", "acceptance"}:
        suspicious.append(f"PDL_ENV={pdl_env!r} is not 'production' or 'acceptance'")
    if target_classification == "unclassified":
        suspicious.append(
            f"target database '{target_name}' is neither the configured canonical "
            f"name ('{canonical_database_name()}') nor a recognized test/acceptance "
            f"naming pattern"
        )

    return {
        "repo_version": __version__,
        "pdl_env": pdl_env,
        "target_host": parsed.host,
        "target_port": parsed.port,
        "target_database": target_name,
        "target_classification": target_classification,
        "canonical_database_name": canonical_database_name(),
        "schema_version": schema_version,
        "latest_schema_version": LATEST_SCHEMA_VERSION,
        "schema_up_to_date": schema_version == LATEST_SCHEMA_VERSION,
        "sibling_databases": siblings,
        "test_like_sibling_count": sum(
            1 for sibling in siblings if sibling["classification"] == "test-like"
        ),
        "suspicious_config": suspicious,
    }


def _render_doctor_table(report: dict) -> str:
    lines = [
        f"personal-data-lab doctor - repo version {report['repo_version']}",
        f"PDL_ENV: {report['pdl_env']}",
        f"target: {report['target_database']} @ {report['target_host']}:{report['target_port']}",
        (
            f"target classification: {report['target_classification']} "
            f"(canonical name configured as '{report['canonical_database_name']}')"
        ),
        (
            f"schema version: {report['schema_version']} "
            f"({'up to date' if report['schema_up_to_date'] else 'OUTDATED'}, "
            f"latest={report['latest_schema_version']})"
        ),
        "",
        (
            f"sibling databases on this instance ({len(report['sibling_databases'])} found, "
            f"{report['test_like_sibling_count']} test-like):"
        ),
    ]
    for sibling in report["sibling_databases"]:
        marker = " <- THIS TARGET" if sibling["is_target"] else ""
        lines.append(
            f"  {sibling['name']:<50} {sibling['classification']:<12} "
            f"{sibling['size_bytes']:>14,} bytes{marker}"
        )
    lines.append("")
    if report["suspicious_config"]:
        lines.append("suspicious config:")
        lines.extend(f"  - {item}" for item in report["suspicious_config"])
    else:
        lines.append("suspicious config: none")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "import-openai":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=True)
        source = Path(args.source).expanduser()
        if source.is_file() and source.name.casefold() == "conversations.json":
            parsed = parse_openai_export(source)
            result = import_openai_export(engine, parsed)
            print(json.dumps(result.model_dump() | {"reconciled": result.reconciled}, indent=2))
            return 2 if result.has_conflicts else 0
        summary = import_openai_all(
            engine,
            args.source,
            on_member_start=_openai_member_started,
            on_member_imported=_openai_member_imported,
        )
        for item in summary.coverage.values():
            if item.failures:
                print(
                    f"[openai] failed member_id={item.member_id} "
                    f"category={item.accounting_category}",
                    file=sys.stderr,
                    flush=True,
                )
        print(
            json.dumps(summary.model_dump(mode="json") | {"exit_code": summary.exit_code}, indent=2)
        )
        return summary.exit_code
    if args.command == "inspect-openai":
        inventory = inspect_openai(args.source)
        if args.output_format == "table":
            print(
                "\n".join(
                    (
                        f"physical members: {inventory.physical_members_total}",
                        f"logical files: {inventory.logical_files_total}",
                        f"conversation shards: {inventory.conversation_shards}",
                        f"structured JSON files: {inventory.structured_json_files}",
                        f"binary assets: {inventory.binary_asset_files}",
                        f"rendered HTML files: {inventory.rendered_html_files}",
                        f"unknown files: {inventory.unknown_files}",
                    )
                )
            )
        else:
            print(json.dumps(inventory.public_dict(include_paths=args.include_paths), indent=2))
        return 5 if inventory.unknown_files else 0
    if args.command in {"import-anthropic", "import-anthropic-all"}:
        engine = _resolve_guarded_engine(args.database_url, canonical_import=True)
        summary = import_anthropic_all(
            engine,
            args.manifest,
            on_member_start=_anthropic_member_started,
            on_member_imported=_anthropic_member_imported,
        )
        print(json.dumps(summary.model_dump(mode="json") | {"exit_code": summary.exit_code}, indent=2))
        return summary.exit_code
    if args.command == "inspect-anthropic":
        inventory = inspect_anthropic(args.manifest)
        if args.output_format == "table":
            print(render_anthropic_table(inventory))
        else:
            print(json.dumps(inventory.public_dict(include_paths=args.include_paths), indent=2))
        return 5 if inventory.unknown_files else 0
    if args.command == "import-google":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=True)
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
        engine = _resolve_guarded_engine(args.database_url, canonical_import=True)
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
    if args.command == "inspect-apple":
        inventory = inspect_apple(args.source)
        if args.output_format == "table":
            print(render_apple_table(inventory))
        else:
            print(json.dumps(inventory.public_dict(include_paths=args.include_paths), indent=2))
        return 5 if inventory.unknown_members_count else 0
    if args.command == "import-apple-all":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=True)
        summary = import_apple_all(
            engine,
            args.source,
            on_member_start=_apple_member_started,
            on_member_imported=_apple_member_imported,
        )
        for product in summary.coverage.values():
            for member_id in product.failed_member_ids:
                print(
                    f"[apple-all] failed member_id={member_id} product={product.product_key}",
                    file=sys.stderr,
                    flush=True,
                )
        print(
            json.dumps(summary.model_dump(mode="json") | {"exit_code": summary.exit_code}, indent=2)
        )
        return summary.exit_code
    if args.command == "inspect-facebook":
        inventory = inspect_facebook(args.source)
        if args.output_format == "table":
            print(render_facebook_table(inventory))
        else:
            print(json.dumps(inventory.public_dict(include_paths=args.include_paths), indent=2))
        return 5 if inventory.unknown_members_count else 0
    if args.command == "import-facebook-all":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=True)
        summary = import_facebook_all(
            engine,
            args.source,
            on_member_start=_facebook_member_started,
            on_member_imported=_facebook_member_imported,
        )
        for product in summary.coverage.values():
            for member_id in product.failed_member_ids:
                print(
                    f"[facebook-all] failed member_id={member_id} product={product.product_key}",
                    file=sys.stderr,
                    flush=True,
                )
        print(
            json.dumps(summary.model_dump(mode="json") | {"exit_code": summary.exit_code}, indent=2)
        )
        return summary.exit_code
    if args.command == "inspect-imessages":
        inventory = inspect_imessages(args.source)
        if args.output_format == "table":
            print(render_imessages_table(inventory))
        else:
            print(json.dumps(inventory.public_dict(include_paths=args.include_paths), indent=2))
        return 5 if inventory.unknown_files else 0
    if args.command == "import-imessages-all":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=True)
        summary = import_imessages_all(
            engine,
            args.source,
            timezone_name=args.timezone,
            device_label=args.device_label,
            on_member_start=_imessages_member_started,
            on_member_imported=_imessages_member_imported,
        )
        for item in summary.coverage.values():
            if item.failures:
                print(
                    f"[imessages] failed member_id={item.member_id}",
                    file=sys.stderr,
                    flush=True,
                )
        print(
            json.dumps(summary.model_dump(mode="json") | {"exit_code": summary.exit_code}, indent=2)
        )
        return summary.exit_code
    if args.command == "migrate":
        engine = _resolve_guarded_engine(args.database_url, canonical_import=False)
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
    if args.command == "doctor":
        report = _build_doctor_report(args.database_url)
        if args.output_format == "table":
            print(_render_doctor_table(report))
        else:
            print(json.dumps(report, indent=2))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
